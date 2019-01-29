"""
Regression semi-supervised GAN code.
"""
import datetime
import os
import re
import select
import sys
from abc import ABC, abstractmethod

import numpy as np
from scipy.stats import norm
from torch.nn import Module
from torch.optim import Adam, Optimizer
import torch
from torch.utils.data import Dataset, DataLoader
from torch import Tensor

from settings import Settings
from utility import SummaryWriter, gpu, make_directory_name_unique, MixtureModel, seed_all, norm_squared, square_mean


class Experiment(ABC):
    """A class to manage an experimental trial."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.trial_directory: str = None
        self.dnn_summary_writer: SummaryWriter = None
        self.gan_summary_writer: SummaryWriter = None
        self.dataset_class = None
        self.train_dataset: Dataset = None
        self.train_dataset_loader: DataLoader = None
        self.unlabeled_dataset: Dataset = None
        self.unlabeled_dataset_loader: DataLoader = None
        self.validation_dataset: Dataset = None
        self.DNN: Module = None
        self.dnn_optimizer: Optimizer = None
        self.D: Module = None
        self.d_optimizer: Optimizer = None
        self.G: Module = None
        self.g_optimizer: Optimizer = None
        self.signal_quit = False

        self.labeled_features = None
        self.unlabeled_features = None
        self.fake_features = None
        self.interpolates_features = None
        self.gradient_norm = None

    def train(self):
        """
        Run the SRGAN training for the experiment.
        """
        self.trial_directory = os.path.join(self.settings.logs_directory, self.settings.trial_name)
        if (self.settings.skip_completed_experiment and os.path.exists(self.trial_directory) and
                '/check' not in self.trial_directory):
            print('`{}` experiment already exists. Skipping...'.format(self.trial_directory))
            return
        self.trial_directory = make_directory_name_unique(self.trial_directory)
        print(self.trial_directory)
        os.makedirs(os.path.join(self.trial_directory, self.settings.temporary_directory))
        self.prepare_summary_writers()
        seed_all(0)

        self.dataset_setup()
        self.model_setup()
        self.load_models()
        self.gpu_mode()
        self.train_mode()
        self.prepare_optimizers()

        self.training_loop()

        print('Completed {}'.format(self.trial_directory))
        if self.settings.should_save_models:
            self.save_models()

    def save_models(self, step=None):
        """Saves the network models."""
        if step is not None:
            suffix = '_{}'.format(step)
        else:
            suffix = ''
        torch.save(self.DNN.state_dict(), os.path.join(self.trial_directory, 'DNN_model{}.pth'.format(suffix)))
        torch.save(self.D.state_dict(), os.path.join(self.trial_directory, 'D_model{}.pth'.format(suffix)))
        torch.save(self.G.state_dict(), os.path.join(self.trial_directory, 'G_model{}.pth'.format(suffix)))

    def training_loop(self):
        """Runs the main training loop."""
        train_dataset_generator = self.infinite_iter(self.train_dataset_loader)
        unlabeled_dataset_generator = self.infinite_iter(self.unlabeled_dataset_loader)
        step_time_start = datetime.datetime.now()
        for step in range(self.settings.steps_to_run):
            self.adjust_learning_rate(step)
            # DNN.
            samples = next(train_dataset_generator)
            if len(samples) == 2:
                labeled_examples, labels = samples
                labeled_examples, labels = labeled_examples.to(gpu), labels.to(gpu)
            else:
                labeled_examples, primary_labels, secondary_labels = samples
                labeled_examples, labels = labeled_examples.to(gpu), (primary_labels.to(gpu), secondary_labels.to(gpu))
            self.dnn_training_step(labeled_examples, labels, step)
            # GAN.
            unlabeled_examples = next(unlabeled_dataset_generator)[0]
            unlabeled_examples = unlabeled_examples.to(gpu)
            self.gan_training_step(labeled_examples, labels, unlabeled_examples, step)

            if self.gan_summary_writer.is_summary_step() or step == self.settings.steps_to_run - 1:
                print('\rStep {}, {}...'.format(step, datetime.datetime.now() - step_time_start), end='')
                step_time_start = datetime.datetime.now()
                self.eval_mode()
                with torch.no_grad():
                    self.validation_summaries(step)
                self.train_mode()
            self.handle_user_input(step)

    def prepare_optimizers(self):
        """Prepares the optimizers of the network."""
        d_lr = self.settings.learning_rate
        g_lr = d_lr
        weight_decay = self.settings.weight_decay
        self.d_optimizer = Adam(self.D.parameters(), lr=d_lr, weight_decay=weight_decay)
        self.g_optimizer = Adam(self.G.parameters(), lr=g_lr)
        self.dnn_optimizer = Adam(self.DNN.parameters(), lr=d_lr, weight_decay=weight_decay)

    def prepare_summary_writers(self):
        """Prepares the summary writers for TensorBoard."""
        self.dnn_summary_writer = SummaryWriter(os.path.join(self.trial_directory, 'DNN'))
        self.gan_summary_writer = SummaryWriter(os.path.join(self.trial_directory, 'GAN'))
        self.dnn_summary_writer.summary_period = self.settings.summary_step_period
        self.gan_summary_writer.summary_period = self.settings.summary_step_period

    def handle_user_input(self, step):
        """
        Handle input from the user.

        :param step: The current step of the program.
        :type step: int
        """
        while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            line = sys.stdin.readline()
            if 'save' in line:
                self.save_models(step)
                print('\rSaved model for step {}...'.format(step))
            if 'quit' in line:
                self.signal_quit = True
                print('\rQuit requested after current experiment...')

    def train_mode(self):
        """
        Converts the networks to train mode.
        """
        self.D.train()
        self.DNN.train()
        self.G.train()

    def gpu_mode(self):
        """
        Moves the networks to the GPU (if available).
        """
        self.D.to(gpu)
        self.DNN.to(gpu)
        self.G.to(gpu)

    def eval_mode(self):
        """
        Changes the network to evaluation mode.
        """
        self.D.eval()
        self.DNN.eval()
        self.G.eval()

    def cpu_mode(self):
        """
        Moves the networks to the CPU.
        """
        self.D.to('cpu')
        self.DNN.to('cpu')
        self.G.to('cpu')

    @staticmethod
    def compare_model_path_for_latest(model_path1, model_path2):
        """
        Compares two version of the model path to see which one has trained longer. A model without any step number
        is considered to have trained the longest.

        :param model_path1: The first model path.
        :type model_path1: re.Match
        :param model_path2: The second model path.
        :type model_path2: re.Match
        :return: The model path which was newer.
        :rtype: re.Match
        """
        if model_path1 is None:
            return model_path2
        elif model_path1.group(2) is None:
            return model_path1
        elif model_path2.group(2) is None:
            return model_path2
        elif int(model_path1.group(2)) > int(model_path2.group(2)):
            return model_path1
        else:
            return model_path2

    def load_models(self):
        """Loads existing models if they exist at `self.settings.load_model_path`."""
        if self.settings.load_model_path:
            latest_dnn_model = None
            latest_d_model = None
            latest_g_model = None
            model_path_file_names = os.listdir(self.settings.load_model_path)
            for file_name in model_path_file_names:
                match = re.search(r'(DNN|D|G)_model_?(\d+)?\.pth', file_name)
                if match:
                    if match.group(1) == 'DNN':
                        latest_dnn_model = self.compare_model_path_for_latest(latest_dnn_model, match)
                    elif match.group(1) == 'D':
                        latest_d_model = self.compare_model_path_for_latest(latest_d_model, match)
                    elif match.group(1) == 'G':
                        latest_g_model = self.compare_model_path_for_latest(latest_g_model, match)
            latest_dnn_model = None if latest_dnn_model is None else latest_dnn_model.group(0)
            latest_d_model = None if latest_d_model is None else latest_d_model.group(0)
            latest_g_model = None if latest_g_model is None else latest_g_model.group(0)
            if not torch.cuda.is_available():
                map_location = 'cpu'
            else:
                map_location = None
            if latest_dnn_model:
                dnn_model_path = os.path.join(self.settings.load_model_path, latest_dnn_model)
                print('DNN model loaded from `{}`.'.format(dnn_model_path))
                self.DNN.load_state_dict(torch.load(dnn_model_path, map_location))
            if latest_d_model:
                d_model_path = os.path.join(self.settings.load_model_path, latest_d_model)
                print('D model loaded from `{}`.'.format(d_model_path))
                self.D.load_state_dict(torch.load(d_model_path, map_location))
            if latest_g_model:
                g_model_path = os.path.join(self.settings.load_model_path, latest_g_model)
                print('G model loaded from `{}`.'.format(g_model_path))
                self.G.load_state_dict(torch.load(g_model_path, map_location))

    def dnn_training_step(self, examples, labels, step):
        """Runs an individual round of DNN training."""
        self.DNN.apply(disable_batch_norm_updates)  # No batch norm
        self.dnn_summary_writer.step = step
        self.dnn_optimizer.zero_grad()
        dnn_loss = self.dnn_loss_calculation(examples, labels)
        dnn_loss.backward()
        self.dnn_optimizer.step()
        # Summaries.
        if self.dnn_summary_writer.is_summary_step():
            self.dnn_summary_writer.add_scalar('Discriminator/Labeled Loss', dnn_loss.item())
            if hasattr(self.DNN, 'features') and self.DNN.features is not None:
                self.dnn_summary_writer.add_scalar('Feature Norm/Labeled', self.DNN.features.norm(dim=1).mean().item())

    def gan_training_step(self, labeled_examples, labels, unlabeled_examples, step):
        """Runs an individual round of GAN training."""
        # Labeled.
        self.D.apply(disable_batch_norm_updates)  # No batch norm
        self.gan_summary_writer.step = step
        self.d_optimizer.zero_grad()
        loss = torch.tensor(0, dtype=torch.float, device=gpu)
        labeled_loss = self.labeled_loss_calculation(labeled_examples, labels)
        #loss += labeled_loss
        labeled_loss.backward()
        # Unlabeled.
        #self.D.apply(disable_batch_norm_updates)  # Make sure only labeled data is used for batch norm statistics
        unlabeled_loss = self.unlabeled_loss_calculation(labeled_examples, unlabeled_examples)
        # loss += unlabeled_loss
        unlabeled_loss.backward()
        # Feature regularization loss.
        if self.settings.regularize_feature_norm:
            feature_regularization_loss = torch.abs(self.unlabeled_features.mean(0).norm() - 1)
            loss += feature_regularization_loss
        # Fake.
        z = torch.tensor(MixtureModel([norm(-self.settings.mean_offset, 1),
                                       norm(self.settings.mean_offset, 1)]
                                      ).rvs(size=[unlabeled_examples.size(0),
                                                  self.G.input_size]).astype(np.float32)).to(gpu)
        fake_examples = self.G(z)
        fake_loss = self.fake_loss_calculation(unlabeled_examples, fake_examples)
        #loss += fake_loss
        fake_loss.backward()
        # Gradient penalty.
        gradient_penalty = self.gradient_penalty_calculation(fake_examples, unlabeled_examples)
        #loss += gradient_penalty
        gradient_penalty.backward()
        # Discriminator update.
        #loss.backward()
        self.d_optimizer.step()
        # Generator.
        if step % self.settings.generator_training_step_period == 0:
            self.g_optimizer.zero_grad()
            z = torch.randn(unlabeled_examples.size(0), self.G.input_size).to(gpu)
            fake_examples = self.G(z)
            generator_loss = self.generator_loss_calculation(fake_examples, unlabeled_examples)
            generator_loss.backward()
            self.g_optimizer.step()
            if self.gan_summary_writer.is_summary_step():
                self.gan_summary_writer.add_scalar('Generator/Loss', generator_loss.item())
        # Summaries.
        if self.gan_summary_writer.is_summary_step():
            self.gan_summary_writer.add_scalar('Discriminator/Labeled Loss', labeled_loss.item())
            self.gan_summary_writer.add_scalar('Discriminator/Unlabeled Loss', unlabeled_loss.item())
            self.gan_summary_writer.add_scalar('Discriminator/Fake Loss', fake_loss.item())
            self.gan_summary_writer.add_scalar('Discriminator/Gradient Penalty', gradient_penalty.item())
            self.gan_summary_writer.add_scalar('Discriminator/Gradient Norm', self.gradient_norm.mean().item())
            if self.labeled_features is not None:
                self.gan_summary_writer.add_scalar('Feature Norm/Labeled',
                                                   self.labeled_features.mean(0).norm().item())
                self.gan_summary_writer.add_scalar('Feature Norm/Unlabeled',
                                                   self.unlabeled_features.mean(0).norm().item())
        #self.D.apply(enable_batch_norm_updates)  # Make sure only labeled data is used for batch norm running statistics

    def dnn_loss_calculation(self, labeled_examples, labels):
        """Calculates the DNN loss."""
        predicted_labels = self.DNN(labeled_examples)
        labeled_loss = self.labeled_loss_function(predicted_labels, labels, order=self.settings.labeled_loss_order)
        labeled_loss *= self.settings.labeled_loss_multiplier
        return labeled_loss

    def labeled_loss_calculation(self, labeled_examples, labels):
        """Calculates the labeled loss."""
        predicted_labels = self.D(labeled_examples)
        self.labeled_features = self.D.features
        labeled_loss = self.labeled_loss_function(predicted_labels, labels, order=self.settings.labeled_loss_order)
        labeled_loss *= self.settings.labeled_loss_multiplier
        return labeled_loss

    def unlabeled_loss_calculation(self, labeled_examples: Tensor, unlabeled_examples: Tensor):
        """Calculates the unlabeled loss."""
        _ = self.D(labeled_examples)
        self.labeled_features = self.D.features
        _ = self.D(unlabeled_examples)
        self.unlabeled_features = self.D.features
        unlabeled_loss = feature_distance_loss(self.unlabeled_features, self.labeled_features
                                               ) * self.settings.unlabeled_loss_multiplier
        unlabeled_loss *= self.settings.srgan_loss_multiplier
        return unlabeled_loss

    def fake_loss_calculation(self, unlabeled_examples: Tensor, fake_examples: Tensor):
        """Calculates the fake loss."""
        _ = self.D(unlabeled_examples)
        self.unlabeled_features = self.D.features
        _ = self.D(fake_examples.detach())
        self.fake_features = self.D.features
        fake_loss = feature_distance_loss(self.unlabeled_features, self.fake_features,
                                          distance_function=self.settings.fake_loss_distance)
        fake_loss *= self.settings.fake_loss_multiplier
        fake_loss *= self.settings.srgan_loss_multiplier
        return fake_loss

    def gradient_penalty_calculation(self, fake_examples: Tensor, unlabeled_examples: Tensor) -> Tensor:
        """Calculates the gradient penalty from the given fake and real examples."""
        alpha_shape = [1] * len(unlabeled_examples.size())
        alpha_shape[0] = self.settings.batch_size
        alpha = torch.rand(alpha_shape, device=gpu)
        interpolates = (alpha * unlabeled_examples.detach().requires_grad_() +
                        (1 - alpha) * fake_examples.detach().requires_grad_())
        interpolates_loss = self.interpolate_loss_calculation(interpolates)
        gradients = torch.autograd.grad(outputs=interpolates_loss, inputs=interpolates,
                                        grad_outputs=torch.ones_like(interpolates_loss, device=gpu),
                                        create_graph=True)[0]
        gradient_norm = gradients.view(unlabeled_examples.size(0), -1).norm(dim=1)
        self.gradient_norm = gradient_norm
        norm_excesses = torch.max(gradient_norm - 1, torch.zeros_like(gradient_norm))
        gradient_penalty = (norm_excesses ** 2).mean() * self.settings.gradient_penalty_multiplier
        return gradient_penalty

    def interpolate_loss_calculation(self, interpolates):
        """Calculates the interpolate loss for use in the gradient penalty."""
        _ = self.D(interpolates)
        self.interpolates_features = self.D.features
        return self.interpolates_features.norm(dim=1)

    def generator_loss_calculation(self, fake_examples, unlabeled_examples):
        """Calculates the generator's loss."""
        _ = self.D(fake_examples)
        self.fake_features = self.D.features
        _ = self.D(unlabeled_examples)
        detached_unlabeled_features = self.D.features.detach()
        generator_loss = feature_distance_loss(detached_unlabeled_features, self.fake_features)
        return generator_loss

    @abstractmethod
    def dataset_setup(self):
        """Prepares all the datasets and loaders required for the application."""
        self.train_dataset = Dataset()
        self.unlabeled_dataset = Dataset()
        self.validation_dataset = Dataset()
        self.train_dataset_loader = DataLoader(self.train_dataset)
        self.unlabeled_dataset_loader = DataLoader(self.validation_dataset)

    @abstractmethod
    def model_setup(self):
        """Prepares all the model architectures required for the application."""
        self.DNN = Module()
        self.D = Module()
        self.G = Module()

    @abstractmethod
    def validation_summaries(self, step: int):
        """Prepares the summaries that should be run for the given application."""
        pass

    @staticmethod
    def labeled_loss_function(predicted_labels, labels, order=2):
        """Calculate the loss from the label difference prediction."""
        return (predicted_labels - labels).abs().pow(order).mean()

    def evaluate(self):
        """Evaluates the model on the test dataset (needs to be overridden by subclass."""
        self.model_setup()
        self.load_models()
        self.eval_mode()

    @staticmethod
    def infinite_iter(dataset):
        """Create an infinite generator from a dataset. Forces full batch sizes."""
        while True:
            for examples in dataset:
                yield examples

    def adjust_learning_rate(self, step):
        """Sets the learning rate to the initial LR decayed by 10 every 30 epochs"""
        lr = self.settings.learning_rate * (0.1 ** (step // 100000))
        for param_group in self.dnn_optimizer.param_groups:
            param_group['lr'] = lr


def unit_vector(vector):
    """Gets the unit vector version of a vector."""
    return vector.div(vector.norm() + 1e-10)


def angle_between(vector0, vector1):
    """Calculates the angle between two vectors."""
    unit_vector0 = unit_vector(vector0)
    unit_vector1 = unit_vector(vector1)
    epsilon = 1e-6
    return unit_vector0.dot(unit_vector1).clamp(-1.0 + epsilon, 1.0 - epsilon).acos()


def square(tensor):
    """Squares the tensor value."""
    return tensor.pow(2)


def feature_distance_loss(base_features, other_features, distance_function=square_mean):
    """Calculate the loss based on the distance between feature vectors."""
    base_mean_features = base_features.mean(0)
    other_mean_features = other_features.mean(0)
    distance_vector = distance_function(base_mean_features - other_mean_features)
    return distance_vector


def feature_distance_loss_unmeaned(base_features, other_features, distance_function=square):
    """Calculate the loss based on the distance between feature vectors."""
    base_mean_features = base_features.mean(0, keepdim=True)
    distance_vector = distance_function(base_mean_features - other_features)
    return distance_vector.mean()


def feature_distance_loss_both_unmeaned(base_features, other_features, distance_function=norm_squared):
    """Calculate the loss based on the distance between feature vectors."""
    distance_vector = distance_function(base_features - other_features)
    return distance_vector.mean()


def feature_angle_loss(base_features, other_features, target=0, summary_writer=None):
    """Calculate the loss based on the angle between feature vectors."""
    angle = angle_between(base_features.mean(0), other_features.mean(0))
    if summary_writer:
        summary_writer.add_scalar('Feature Vector/Angle', angle.item(), )
    return (angle - target).abs().pow(2)


def feature_corrcoef(x):
    """Calculate the feature vector's correlation coefficients."""
    transposed_x = x.transpose(0, 1)
    return corrcoef(transposed_x)


def corrcoef(x):
    """Calculate the correlation coefficients."""
    mean_x = x.mean(1, keepdim=True)
    xm = x.sub(mean_x)
    c = xm.mm(xm.t())
    c = c / (x.size(1) - 1)
    d = torch.diag(c)
    stddev = torch.pow(d, 0.5)
    c = c.div(stddev.expand_as(c))
    c = c.div(stddev.expand_as(c).t())
    c = torch.clamp(c, -1.0, 1.0)
    return c


def feature_covariance_loss(base_features, other_features):
    """Calculate the loss between feature vector correlation coefficient distances."""
    base_corrcoef = feature_corrcoef(base_features)
    other_corrcoef = feature_corrcoef(other_features)
    return (base_corrcoef - other_corrcoef).abs().sum()


def disable_batch_norm_updates(module):
    """Turns off updating of batch norm statistics."""
    # noinspection PyProtectedMember
    if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
        module.eval()


def enable_batch_norm_updates(module):
    """Turns on updating of batch norm statistics."""
    # noinspection PyProtectedMember
    if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
        module.train()

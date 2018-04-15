"""
Regression semi-supervised GAN code.
"""
import datetime
import os
import select
import sys

import numpy as np
from scipy.stats import norm, wasserstein_distance
from torch.autograd import Variable
from torch.optim import Adam
from torch.utils.data import DataLoader
import torch

from coefficient_models import observation_count, Generator, MLP
from settings import Settings
from data import ToyDataset, MixtureModel, seed_all
from hardware import gpu, cpu
from presentation import generate_display_frame
from training_functions import dnn_training_step, gan_training_step
from utility import SummaryWriter, infinite_iter, clean_scientific_notation, shuffled

global_trial_directory = None


def run_srgan(settings):
    """
    Train the SRGAN

    :param settings: The settings object.
    :type settings: Settings
    """
    datetime_string = datetime.datetime.now().strftime('y%Ym%md%dh%Hm%Ms%S')
    trial_directory = os.path.join(settings.logs_directory, '{} {}'.format(settings.trial_name, datetime_string))
    global global_trial_directory
    global_trial_directory = trial_directory
    os.makedirs(os.path.join(trial_directory, settings.temporary_directory))
    dnn_summary_writer = SummaryWriter(os.path.join(trial_directory, 'DNN'))
    gan_summary_writer = SummaryWriter(os.path.join(trial_directory, 'GAN'))
    dnn_summary_writer.summary_period = settings.summary_step_period
    gan_summary_writer.summary_period = settings.summary_step_period

    train_dataset = ToyDataset(dataset_size=settings.labeled_dataset_size, observation_count=observation_count,
                               seed=settings.labeled_dataset_seed)
    train_dataset_loader = DataLoader(train_dataset, batch_size=settings.batch_size, shuffle=True)

    unlabeled_dataset = ToyDataset(dataset_size=settings.unlabeled_dataset_size, observation_count=observation_count,
                                   seed=1)
    unlabeled_dataset_loader = DataLoader(unlabeled_dataset, batch_size=settings.batch_size, shuffle=True)

    test_dataset = ToyDataset(settings.test_dataset_size, observation_count, seed=2)

    G_model = gpu(Generator())
    D_mlp = MLP()
    DNN_mlp = MLP()
    if settings.load_model_path:
        if not torch.cuda.is_available():
            map_location = 'cpu'
        else:
            map_location = None
        DNN_mlp.load_state_dict(torch.load(os.path.join(settings.load_model_path, 'DNN_model.pth'), map_location))
        D_mlp.load_state_dict(torch.load(os.path.join(settings.load_model_path, 'D_model.pth'), map_location))
        G_model.load_state_dict(torch.load(os.path.join(settings.load_model_path, 'G_model.pth'), map_location))
    G = gpu(G_model)
    D = gpu(D_mlp)
    DNN = gpu(DNN_mlp)
    d_lr = settings.learning_rate
    g_lr = d_lr

    betas = (0.9, 0.999)
    weight_decay = 1e-2
    D_optimizer = Adam(D.parameters(), lr=d_lr, weight_decay=weight_decay)
    G_optimizer = Adam(G.parameters(), lr=g_lr)
    DNN_optimizer = Adam(DNN.parameters(), lr=d_lr, weight_decay=weight_decay)

    step_time_start = datetime.datetime.now()
    print(trial_directory)
    train_dataset_generator = infinite_iter(train_dataset_loader)
    unlabeled_dataset_generator = infinite_iter(unlabeled_dataset_loader)

    for step in range(settings.steps_to_run):
        if step % settings.summary_step_period == 0 and step != 0:
            print('\rStep {}, {}...'.format(step, datetime.datetime.now() - step_time_start), end='')
            step_time_start = datetime.datetime.now()
        # DNN.
        labeled_examples, labels = next(train_dataset_generator)
        dnn_training_step(DNN, DNN_optimizer, dnn_summary_writer, labeled_examples, labels, settings, step)
        # GAN.
        unlabeled_examples, _ = next(unlabeled_dataset_generator)
        gan_training_step(D, D_optimizer, G, G_optimizer, gan_summary_writer, labeled_examples, labels, settings, step,
                          unlabeled_examples)

        if (dnn_summary_writer.step % dnn_summary_writer.summary_period == 0 or
                dnn_summary_writer.step % settings.presentation_step_period == 0):
            dnn_predicted_train_labels = cpu(DNN(gpu(Variable(torch.from_numpy(
                train_dataset.examples.astype(np.float32))))).data).numpy()
            dnn_train_label_errors = np.mean(np.abs(dnn_predicted_train_labels - train_dataset.labels), axis=0)
            dnn_summary_writer.add_scalar('2 Train Error/MAE', dnn_train_label_errors.data[0])
            dnn_predicted_test_labels = cpu(DNN(gpu(Variable(torch.from_numpy(
                test_dataset.examples.astype(np.float32))))).data).numpy()
            dnn_test_label_errors = np.mean(np.abs(dnn_predicted_test_labels - test_dataset.labels), axis=0)
            dnn_summary_writer.add_scalar('1 Test Error/MAE', dnn_test_label_errors.data[0])

            predicted_train_labels = cpu(D(gpu(Variable(torch.from_numpy(
                train_dataset.examples.astype(np.float32))))).data).numpy()
            gan_train_label_errors = np.mean(np.abs(predicted_train_labels - train_dataset.labels), axis=0)
            gan_summary_writer.add_scalar('2 Train Error/MAE', gan_train_label_errors.data[0])
            predicted_test_labels = cpu(D(gpu(Variable(torch.from_numpy(
                test_dataset.examples.astype(np.float32))))).data).numpy()
            gan_test_label_errors = np.mean(np.abs(predicted_test_labels - test_dataset.labels), axis=0)
            gan_summary_writer.add_scalar('1 Test Error/MAE', gan_test_label_errors.data[0])
            gan_summary_writer.add_scalar('1 Test Error/Ratio MAE GAN DNN',
                                          gan_test_label_errors.data[0] / dnn_test_label_errors.data[0])

            z = torch.from_numpy(MixtureModel([norm(-settings.mean_offset, 1), norm(settings.mean_offset, 1)]).rvs(
                size=[settings.batch_size, G.input_size]).astype(np.float32))
            fake_examples = G(gpu(Variable(z)), add_noise=False)
            fake_examples_array = cpu(fake_examples.data).numpy()
            fake_labels_array = np.mean(fake_examples_array, axis=1)
            unlabeled_labels_array = unlabeled_dataset.labels[:settings.test_dataset_size][:, 0]
            label_wasserstein_distance = wasserstein_distance(fake_labels_array, unlabeled_labels_array)
            gan_summary_writer.add_scalar('Generator/Label Wasserstein', label_wasserstein_distance)

            unlabeled_examples_array = unlabeled_dataset.examples[:settings.test_dataset_size]
            unlabeled_examples = torch.from_numpy(unlabeled_examples_array.astype(np.float32))
            unlabeled_predictions = D(gpu(Variable(unlabeled_examples)))

            if dnn_summary_writer.step % settings.presentation_step_period == 0:
                unlabeled_predictions_array = cpu(unlabeled_predictions.data).numpy()
                test_predictions_array = predicted_test_labels
                train_predictions_array = predicted_train_labels
                dnn_test_predictions_array = dnn_predicted_test_labels
                dnn_train_predictions_array = dnn_predicted_train_labels
                distribution_image = generate_display_frame(trial_directory, fake_examples_array,
                                                            unlabeled_predictions_array, test_predictions_array,
                                                            dnn_test_predictions_array, train_predictions_array,
                                                            dnn_train_predictions_array, step)
                gan_summary_writer.add_image('Distributions', distribution_image)
            while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if 'save' in line:
                    torch.save(DNN.state_dict(), os.path.join(trial_directory, 'DNN_model_{}.pth'.format(step)))
                    torch.save(D.state_dict(), os.path.join(trial_directory, 'D_model_{}.pth'.format(step)))
                    torch.save(G.state_dict(), os.path.join(trial_directory, 'G_model_{}.pth'.format(step)))
                    print('\rSaved model for step {}...'.format(step))

    print('Completed {}'.format(trial_directory))
    if settings.should_save_models:
        torch.save(DNN.state_dict(), os.path.join(trial_directory, 'DNN_model.pth'))
        torch.save(D.state_dict(), os.path.join(trial_directory, 'D_model.pth'))
        torch.save(G.state_dict(), os.path.join(trial_directory, 'G_model.pth'))


for labeled_dataset_seed in shuffled([0]):#, 3, 4, 5, 6]):
    for labeled_dataset_size in shuffled([100]):#, 10, 30, 100, 300, 1000]):
        for mean_offset in shuffled([2]):
            for unlabeled_multiplier in shuffled([1e0]):
                for fake_multiplier in shuffled([1e-1]):
                    for gradient_penalty_multiplier in shuffled([1e1]):
                        for learning_rate in shuffled([1e-5]):
                            for unlabeled_loss_order in shuffled([2]):
                                for fake_loss_order in shuffled([1]):
                                    for generator_loss_order in shuffled([2]):
                                        seed_all(0)
                                        scale_multiplier = 1e0
                                        fake_multiplier = fake_multiplier * scale_multiplier
                                        unlabeled_multiplier = unlabeled_multiplier * scale_multiplier
                                        settings_ = Settings()
                                        settings_.fake_loss_multiplier = fake_multiplier
                                        settings_.unlabeled_loss_multiplier = unlabeled_multiplier
                                        settings_.steps_to_run = 500000
                                        settings_.learning_rate = learning_rate
                                        settings_.labeled_dataset_size = labeled_dataset_size
                                        settings_.gradient_penalty_multiplier = gradient_penalty_multiplier
                                        settings_.norm_loss_multiplier = 0
                                        settings_.mean_offset = mean_offset
                                        settings_.fake_loss_order = fake_loss_order
                                        settings_.unlabeled_loss_order = unlabeled_loss_order
                                        settings_.generator_loss_order = generator_loss_order
                                        settings_.generator_training_step_period = 1
                                        settings_.should_save_models = True
                                        settings_.labeled_dataset_seed = labeled_dataset_seed
                                        settings_.load_model_path = '/home/golmschenk/srgan/logs/detachall ul1e0 fl1e-1 le100 gp1e1 bg1e0 lr1e-5 nl0 gs1 ls0 u2f1g2 l y2018m04d15h14m14s10'
                                        trial_name = 'detachall c'
                                        trial_name += ' ul{:e}'.format(unlabeled_multiplier)
                                        trial_name += ' fl{:e}'.format(fake_multiplier)
                                        trial_name += ' le{}'.format(settings_.labeled_dataset_size)
                                        trial_name += ' gp{:e}'.format(settings_.gradient_penalty_multiplier)
                                        trial_name += ' bg{:e}'.format(settings_.mean_offset)
                                        trial_name += ' lr{:e}'.format(settings_.learning_rate)
                                        trial_name += ' nl{}'.format(settings_.norm_loss_multiplier)
                                        trial_name += ' gs{}'.format(settings_.generator_training_step_period)
                                        trial_name += ' ls{}'.format(settings_.labeled_dataset_seed)
                                        trial_name += ' u{}f{}g{}'.format(settings_.unlabeled_loss_order,
                                                                          settings_.fake_loss_order,
                                                                          settings_.generator_loss_order)
                                        trial_name += ' l' if settings_.load_model_path else ''
                                        settings_.trial_name = clean_scientific_notation(trial_name)
                                        run_srgan(settings_)

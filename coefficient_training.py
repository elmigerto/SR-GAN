import numpy as np
import torch
from scipy.stats import norm, wasserstein_distance
from torch.autograd import Variable
from torch.utils.data import DataLoader

from coefficient_data import ToyDataset
from coefficient_models import Generator, MLP, observation_count

from presentation import generate_display_frame
from utility import cpu, gpu, MixtureModel


def validation_summaries(D, DNN, G, dnn_summary_writer, gan_summary_writer, settings, step, train_dataset,
                         trial_directory, unlabeled_dataset, validation_dataset):
    dnn_predicted_train_labels = cpu(DNN(gpu(Variable(torch.from_numpy(
        train_dataset.examples.astype(np.float32))))).data).numpy()
    dnn_train_label_errors = np.mean(np.abs(dnn_predicted_train_labels - train_dataset.labels), axis=0)
    dnn_summary_writer.add_scalar('2 Train Error/MAE', dnn_train_label_errors.data[0])
    dnn_predicted_validation_labels = cpu(DNN(gpu(Variable(torch.from_numpy(
        validation_dataset.examples.astype(np.float32))))).data).numpy()
    dnn_validation_label_errors = np.mean(np.abs(dnn_predicted_validation_labels - validation_dataset.labels), axis=0)
    dnn_summary_writer.add_scalar('1 Validation Error/MAE', dnn_validation_label_errors.data[0])
    predicted_train_labels = cpu(D(gpu(Variable(torch.from_numpy(
        train_dataset.examples.astype(np.float32))))).data).numpy()
    gan_train_label_errors = np.mean(np.abs(predicted_train_labels - train_dataset.labels), axis=0)
    gan_summary_writer.add_scalar('2 Train Error/MAE', gan_train_label_errors.data[0])
    predicted_validation_labels = cpu(D(gpu(Variable(torch.from_numpy(
        validation_dataset.examples.astype(np.float32))))).data).numpy()
    gan_validation_label_errors = np.mean(np.abs(predicted_validation_labels - validation_dataset.labels), axis=0)
    gan_summary_writer.add_scalar('1 Validation Error/MAE', gan_validation_label_errors.data[0])
    gan_summary_writer.add_scalar('1 Validation Error/Ratio MAE GAN DNN',
                                  gan_validation_label_errors.data[0] / dnn_validation_label_errors.data[0])
    z = torch.from_numpy(MixtureModel([norm(-settings.mean_offset, 1), norm(settings.mean_offset, 1)]).rvs(
        size=[settings.batch_size, G.input_size]).astype(np.float32))
    fake_examples = G(gpu(Variable(z)), add_noise=False)
    fake_examples_array = cpu(fake_examples.data).numpy()
    fake_labels_array = np.mean(fake_examples_array, axis=1)
    unlabeled_labels_array = unlabeled_dataset.labels[:settings.validation_dataset_size][:, 0]
    label_wasserstein_distance = wasserstein_distance(fake_labels_array, unlabeled_labels_array)
    gan_summary_writer.add_scalar('Generator/Label Wasserstein', label_wasserstein_distance)
    unlabeled_examples_array = unlabeled_dataset.examples[:settings.validation_dataset_size]
    unlabeled_examples = torch.from_numpy(unlabeled_examples_array.astype(np.float32))
    unlabeled_predictions = D(gpu(Variable(unlabeled_examples)))
    if dnn_summary_writer.step % settings.presentation_step_period == 0:
        unlabeled_predictions_array = cpu(unlabeled_predictions.data).numpy()
        validation_predictions_array = predicted_validation_labels
        train_predictions_array = predicted_train_labels
        dnn_validation_predictions_array = dnn_predicted_validation_labels
        dnn_train_predictions_array = dnn_predicted_train_labels
        distribution_image = generate_display_frame(trial_directory, fake_examples_array,
                                                    unlabeled_predictions_array, validation_predictions_array,
                                                    dnn_validation_predictions_array, train_predictions_array,
                                                    dnn_train_predictions_array, step)
        gan_summary_writer.add_image('Distributions', distribution_image)


def model_setup():
    G_model = gpu(Generator())
    D_model = MLP()
    DNN_model = MLP()
    return DNN_model, D_model, G_model


def dataset_setup(settings):
    train_dataset = ToyDataset(dataset_size=settings.labeled_dataset_size, observation_count=observation_count,
                               seed=settings.labeled_dataset_seed)
    train_dataset_loader = DataLoader(train_dataset, batch_size=settings.batch_size, shuffle=True)
    unlabeled_dataset = ToyDataset(dataset_size=settings.unlabeled_dataset_size, observation_count=observation_count,
                                   seed=100)
    unlabeled_dataset_loader = DataLoader(unlabeled_dataset, batch_size=settings.batch_size, shuffle=True)
    validation_dataset = ToyDataset(settings.validation_dataset_size, observation_count, seed=101)
    return train_dataset, train_dataset_loader, unlabeled_dataset, unlabeled_dataset_loader, validation_dataset
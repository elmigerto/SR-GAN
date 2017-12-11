"""
Code for preparing presentation stuff.
"""
import imageio as imageio
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import norm, gamma
import seaborn as sns

sns.set()
dpi = 200


def generate_data_concept_images():
    sns.set_style('dark')
    plt.figure()
    x_axis = np.arange(-3, 3, 0.001)
    plt.plot([-0.5, -0.5], [0, norm.pdf(-0.5, 0, 1)], color=sns.color_palette()[1])
    plt.plot([0.5, 0.5], [0, norm.pdf(0.5, 0, 1)], color=sns.color_palette()[2])
    plt.plot([-0.2, -0.2], [0, norm.pdf(-0.2, 0, 1)], color=sns.color_palette()[4])
    plt.plot(x_axis, norm.pdf(x_axis, 0, 1), color=sns.color_palette()[0])
    plt.savefig('mean_generating_distribution.png', dpi=dpi)

    plt.figure()
    x_axis = np.arange(0, 6, 0.001)
    plt.plot([1, 1], [0, gamma.pdf(1, 2)], color=sns.color_palette()[1])
    plt.plot([0.7, 0.7], [0, gamma.pdf(0.7, 2)], color=sns.color_palette()[2])
    plt.plot([2, 2], [0, gamma.pdf(2, 2)], color=sns.color_palette()[4])
    plt.plot(x_axis, gamma.pdf(x_axis, 2), color=sns.color_palette()[0])
    plt.savefig('std_generating_distribution.png', dpi=dpi)

    plt.figure()
    x_axis = np.arange(-4.5, 4.5, 0.001)
    plt.plot(x_axis, norm.pdf(x_axis, -0.5, 1), color=sns.color_palette()[1])
    plt.plot(x_axis, norm.pdf(x_axis, 0.5, 0.7), color=sns.color_palette()[2])
    plt.plot(x_axis, norm.pdf(x_axis, -0.2, 2), color=sns.color_palette()[4])
    plt.savefig('example_normals.png', dpi=dpi)

    plt.figure()
    x_axis = np.arange(-4, 3, 0.001)
    observations = [-0.97100138, -1.20760565, -1.67125, -0.35949918, 1.04644455,
                    -0.06357208, -1.33066351, -1.06934841, -2.8277416, -0.67354897]
    observation_color = sns.xkcd_rgb['medium grey']
    for observation in observations:
        plt.plot([observation, observation], [0, norm.pdf(observation, -0.5, 1)], color=observation_color)
    plt.plot(x_axis, norm.pdf(x_axis, -0.5, 1), color=sns.color_palette()[1])
    plt.savefig('normal_with_observations.png', dpi=dpi)


def generate_learning_process_images():
    sns.set_style('darkgrid')
    fake_examples = np.load('fake_examples.npy', mmap_mode='r')
    unlabeled_predictions = np.load('unlabeled_predictions.npy', mmap_mode='r')
    test_predictions = np.load('test_predictions.npy', mmap_mode='r')
    dnn_test_predictions = np.load('dnn_test_predictions.npy', mmap_mode='r')
    fake_means = fake_examples.mean(axis=2)
    fake_stds = fake_examples.std(axis=2)

    x_axis_limits = [-4, 4]
    x_axis = np.arange(*x_axis_limits, 0.001)
    for step_index in range(fake_examples.shape[0]):
        figure, axes = plt.subplots()
        axes.plot(x_axis, norm.pdf(x_axis, 0, 1), color=sns.color_palette()[0])
        axes = sns.kdeplot(fake_means[step_index], ax=axes, color=sns.color_palette()[4])
        #axes = sns.kdeplot(unlabeled_predictions[step_index, :, 0], ax=axes, color=sns.color_palette()[1])
        axes = sns.kdeplot(test_predictions[step_index, :, 0], ax=axes, color=sns.color_palette()[2])
        axes = sns.kdeplot(dnn_test_predictions[step_index, :, 0], ax=axes, color=sns.color_palette()[3])
        axes.set_xlim(*x_axis_limits)
        axes.set_ylim(0, 1)
        plt.savefig('presentation/{}.png'.format(step_index), dpi=dpi, ax=axes)
        plt.close(figure)
    video_writer = imageio.get_writer('means.mp4', fps=50)
    for image_index in range(fake_means.shape[0]):
        image = imageio.imread('presentation/{}.png'.format(image_index))
        video_writer.append_data(image)

    x_axis_limits = [0, 7]
    x_axis = np.arange(*x_axis_limits, 0.001)
    for step_index in range(fake_examples.shape[0]):
        figure, axes = plt.subplots()
        axes.plot(x_axis, gamma.pdf(x_axis, 2), color=sns.color_palette()[0])
        axes = sns.kdeplot(fake_stds[step_index], ax=axes, color=sns.color_palette()[4])
        #axes = sns.kdeplot(unlabeled_predictions[step_index, :, 1], ax=axes, color=sns.color_palette()[1])
        axes = sns.kdeplot(test_predictions[step_index, :, 1], ax=axes, color=sns.color_palette()[2])
        axes = sns.kdeplot(dnn_test_predictions[step_index, :, 1], ax=axes, color=sns.color_palette()[3])
        axes.set_xlim(*x_axis_limits)
        axes.set_ylim(0, 1)
        plt.savefig('presentation/{}.png'.format(step_index), dpi=dpi, ax=axes)
        plt.close(figure)
    video_writer = imageio.get_writer('stds.mp4', fps=50)
    for image_index in range(fake_means.shape[0]):
        image = imageio.imread('presentation/{}.png'.format(image_index))
        video_writer.append_data(image)


if __name__ == '__main__':
    # generate_data_concept_images()
    generate_learning_process_images()
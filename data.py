"""
Code for the data generating models.
"""
import numpy as np
from scipy.stats import rv_continuous, norm, gamma


def generate_simple_data(number_of_examples, number_of_observations):
    means = np.random.normal(size=[number_of_examples, 1])
    stds = np.random.gamma(shape=2, size=[number_of_examples, 1])
    examples = np.random.normal(means, stds, size=[number_of_examples, number_of_observations])
    examples.sort(axis=1)
    labels = np.concatenate((means, stds), axis=1)
    return examples, labels


def generate_double_peak_data(number_of_examples, number_of_observations):
    double_peak_normal = MixtureModel([norm(-3, 1), norm(3, 1)])
    double_peak_gamma = MixtureModel([gamma(2), gamma(3, loc=4)])
    means = double_peak_normal.rvs(size=[number_of_examples, 1])
    stds = double_peak_gamma.rvs(size=[number_of_examples, 1])
    examples = np.random.normal(means, stds, size=[number_of_examples, number_of_observations])
    examples.sort(axis=1)
    labels = np.concatenate((means, stds), axis=1)
    return examples, labels


class MixtureModel(rv_continuous):
    def __init__(self, submodels, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submodels = submodels

    def _pdf(self, x, **kwargs):
        pdf = self.submodels[0].pdf(x)
        for submodel in self.submodels[1:]:
            pdf += submodel.pdf(x)
        pdf /= len(self.submodels)
        return pdf

    def rvs(self, size):
        submodel_choices = np.random.randint(len(self.submodels), size=size)
        submodel_samples = [submodel.rvs(size=size) for submodel in self.submodels]
        rvs = np.choose(submodel_choices, submodel_samples)
        return rvs



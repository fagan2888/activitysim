# ActivitySim
# See full license in LICENSE.txt.

from __future__ import division

import logging

import numpy as np
import pandas as pd

import tracing

logger = logging.getLogger(__name__)


EXP_UTIL_MIN = 1e-300
EXP_UTIL_MAX = np.inf

PROB_MIN = 1e-300
PROB_MAX = np.inf

MAX_DUMP = 50


def utils_to_probs(utils, trace_label=None, exponentiated=False):
    """
    Convert a table of utilities to exponentiated probabilities.

    Parameters
    ----------
    utils : pandas.DataFrame
        Rows should be choosers and columns should be alternatives.

    trace_label : str
        label for tracing bad utility or probability values
    Returns
    -------
    probs : pandas.DataFrame
        Will have the same index and columns as `utils`.

    """

    # avoid confusion if wrong args passed by position
    assert isinstance(trace_label, str) or (trace_label is None)
    assert isinstance(exponentiated, bool)

    utils_arr = utils.as_matrix().astype('float')
    if not exponentiated:
        utils_arr = np.exp(utils_arr)

    np.clip(utils_arr, EXP_UTIL_MIN, EXP_UTIL_MAX, out=utils_arr)

    # FIXME - do this after the clip so utils_arr rows don't sum to zero
    # FIXME - when all utilities are large negative numbers
    arr_sum = utils_arr.sum(axis=1)

    bad_utils = np.isinf(arr_sum)
    if bad_utils.any():
        msg = "%s exponentiated utility rows have infinite values" % bad_utils.sum()

        logger.critical(msg)
        # log the indexes of the first MAX_DUMP offending rows
        for i in utils.index[bad_utils][:MAX_DUMP].values:
            logger.critical("infinite value for exponentiated utilities of %s" % i)

        if trace_label:
            label = "%s.utils_to_probs.bad_utils" % trace_label
            logger.critical("dumping offending utilities to %s" % label)
            tracing.write_csv(utils[bad_utils][:MAX_DUMP],
                              file_name=label,
                              transpose=False)

        raise RuntimeError(msg)

    np.divide(
        utils_arr, arr_sum.reshape(len(utils_arr), 1),
        out=utils_arr)
    utils_arr[np.isnan(utils_arr)] = PROB_MIN

    np.clip(utils_arr, PROB_MIN, PROB_MAX, out=utils_arr)

    probs = pd.DataFrame(utils_arr, columns=utils.columns, index=utils.index)

    BAD_PROB_THRESHOLD = 0.001
    bad_probs = \
        probs.sum(axis=1).sub(np.ones(len(probs.index))).abs() \
        > BAD_PROB_THRESHOLD * np.ones(len(probs.index))

    if bad_probs.any():
        msg = "utils_to_probs:  %s probabilities do not sum to 1" % bad_probs.sum()

        logger.critical(msg)

        # log the indexes of the first MAX_DUMP offending rows
        for i in probs.index[bad_probs][:MAX_DUMP].values:
            logger.critical("probabilities do not sum to 1 for %s" % i)

        if trace_label:
            label = "%s.utils_to_probs.bad_probs" % trace_label
            logger.critical("dumping offending probabilities to %s" % label)
            tracing.write_csv(probs[bad_probs][:MAX_DUMP],
                              file_name=label,
                              transpose=False)

        raise RuntimeError(msg)

    return probs


def make_choices(probs, trace_label=None):
    """
    Make choices for each chooser from among a set of alternatives.

    Parameters
    ----------
    probs : pandas.DataFrame
        Rows for choosers and columns for the alternatives from which they
        are choosing. Values are expected to be valid probabilities across
        each row, e.g. they should sum to 1.

    Returns
    -------
    choices : pandas.Series
        Maps chooser IDs (from `probs` index) to a choice, where the choice
        is an index into the columns of `probs`.

    """
    nchoosers = len(probs)

    # probs should sum to 1 across each row

    BAD_PROB_THRESHOLD = 0.001
    bad_probs = \
        probs.sum(axis=1).sub(np.ones(len(probs.index))).abs() \
        > BAD_PROB_THRESHOLD * np.ones(len(probs.index))

    if bad_probs.any():
        msg = "make_choices: %s probabilities do not sum to 1" % bad_probs.sum()

        logger.critical(msg)

        # log the indexes of the first MAX_DUMP offending rows
        for i in probs.index[bad_probs][:MAX_DUMP].values:
            logger.critical("probabilities do not sum to 1 for %s" % i)

        if trace_label:
            label = "%s.make_choices.bad_probs" % trace_label
            logger.critical("dumping offending probabilities to %s" % label)
            tracing.write_csv(probs[bad_probs][:MAX_DUMP],
                              file_name=label,
                              transpose=False)

        raise RuntimeError(msg)

    probs_arr = (
        probs.as_matrix().cumsum(axis=1) - np.random.random((nchoosers, 1)))
    rows, cols = np.where(probs_arr > 0)
    choices = (s.iat[0] for _, s in pd.Series(cols).groupby(rows))
    return pd.Series(choices, index=probs.index)


def interaction_dataset(choosers, alternatives, sample_size=None):
    """
    Combine choosers and alternatives into one table for the purposes
    of creating interaction variables and/or sampling alternatives.

    Parameters
    ----------
    choosers : pandas.DataFrame
    alternatives : pandas.DataFrame
    sample_size : int, optional
        If sampling from alternatives for each chooser, this is
        how many to sample.

    Returns
    -------
    interacted : pandas.DataFrame
        Merged choosers and alternatives with data repeated either
        len(alternatives) or `sample_size` times.

    """
    if not choosers.index.is_unique:
        raise RuntimeError(
            "ERROR: choosers index is not unique, "
            "sample will not work correctly")
    if not alternatives.index.is_unique:
        raise RuntimeError(
            "ERROR: alternatives index is not unique, "
            "sample will not work correctly")

    numchoosers = len(choosers)
    numalts = len(alternatives)
    sample_size = sample_size or numalts

    alts_idx = np.arange(numalts)

    if sample_size < numalts:
        sample = np.concatenate(tuple(
            np.random.choice(alts_idx, sample_size, replace=False)
            for _ in range(numchoosers)))
    else:
        sample = np.tile(alts_idx, numchoosers)

    alts_sample = alternatives.take(sample)
    alts_sample['chooser_idx'] = np.repeat(choosers.index.values, sample_size)

    alts_sample = pd.merge(
        alts_sample, choosers, left_on='chooser_idx', right_index=True,
        suffixes=('', '_r'))

    return alts_sample


class Nest(object):
    """
    Data for a nest-logit node or leaf

    This object is passed on yield when iterate over nest nodes (branch or leaf)
    The nested logit design is stored in a yaml file as a tree of dict objects,
    but using an object to pass the nest data makes the code a little more readable

    An example nest specification is in the example tour mode choice model
    yaml configuration file - example/configs/tour_mode_choice.yaml.
    """

    def __init__(self, name=None, level=0):
        self.name = name
        self.level = level
        self.product_of_coefficients = 1
        self.ancestors = []
        self.alternatives = None
        self.coefficient = 0

    @property
    def is_leaf(self):
        return (self.alternatives is None)

    @property
    def type(self):
        return 'leaf' if self.is_leaf else 'node'

    @classmethod
    def nest_types(cls):
        return ['leaf', 'node']


def _each_nest(spec, parent_nest, post_order):
    """
    Iterate over each nest or leaf node in the tree (of subtree)

    This internal routine is called by each_nest, which presents a slightly higer level interface

    Parameters
    ----------
    spec : dict
        Nest spec dict tree (or subtree when recursing) from the model spec yaml file
    parent_nest : Nest
        nest of parent node (passed to accumulate level, ancestors, and product_of_coefficients)
    post_order : Bool
        Should we iterate over the nodes of the tree in post-order or pre-order?
        (post-order means we yield the alternatives sub-tree before current node.)

    Yields
    -------
        spec_node : dict
            Nest tree spec dict for this node subtree
        nest : Nest
            Nest object with info about the current node (nest or leaf)
    """
    pre_order = not post_order

    level = parent_nest.level + 1

    if isinstance(spec, dict):
        name = spec['name']
        coefficient = spec['coefficient']
        alternatives = [a['name'] if isinstance(a, dict) else a for a in spec['alternatives']]

        nest = Nest(name=name)
        nest.level = parent_nest.level + 1
        nest.coefficient = coefficient
        nest.product_of_coefficients = parent_nest.product_of_coefficients * coefficient
        nest.alternatives = alternatives
        nest.ancestors = parent_nest.ancestors + [name]

        if pre_order:
            yield spec, nest

        # recursively iterate the list of alternatives
        for alternative in spec['alternatives']:
            for sub_node, sub_nest in _each_nest(alternative, nest, post_order):
                yield sub_node, sub_nest

        if post_order:
            yield spec, nest

    elif isinstance(spec, str):
        name = spec

        nest = Nest(name=name)
        nest.level = parent_nest.level + 1
        nest.product_of_coefficients = parent_nest.product_of_coefficients
        nest.ancestors = parent_nest.ancestors + [name]

        yield spec, nest


def each_nest(nest_spec, type=None, post_order=False):
    """
    Iterate over each nest or leaf node in the tree (of subtree)

    Parameters
    ----------
    nest_spec : dict
        Nest tree dict from the model spec yaml file
    type : str
        Nest class type to yield
        None yields all nests
        'leaf' yields only leaf nodes
        'branch' yields only branch nodes
    post_order : Bool
        Should we iterate over the nodes of the tree in post-order or pre-order?
        (post-order means we yield the alternatives sub-tree before current node.)

    Yields
    -------
        nest : Nest
            Nest object with info about the current node (nest or leaf)
    """
    if type is not None and type not in Nest.nest_types():
        raise RuntimeError("Unknown nest type '%s' in call to each_nest" % type)

    for node, nest in _each_nest(nest_spec, parent_nest=Nest(), post_order=post_order):
        if type is None or (type == nest.type):
            yield nest

"""
This module contains cost functions to use with deep Boltzmann machines
(pylearn2.models.dbm).
"""
__authors__ = "Ian Goodfellow"
__copyright__ = "Copyright 2012, Universite de Montreal"
__credits__ = ["Ian Goodfellow"]
__license__ = "3-clause BSD"
__maintainer__ = "Ian Goodfellow"

import warnings

from collections import OrderedDict

from theano.sandbox.rng_mrg import MRG_RandomStreams
from theano import tensor as T

from pylearn2.costs.cost import Cost
from pylearn2.models import dbm
from pylearn2.models.dbm import flatten
from pylearn2.utils import safe_izip
from pylearn2.utils import safe_zip


class VariationalPCD_VarianceReduction(Cost):
    """
    Like pylearn2.costs.dbm.VariationalPCD, indeed a copy-paste of it,
    but with the right variance reduction rule hard-coded for 2 binary
    hidden layers and a softmax label layer
    Right variance reduction rule is to average together the expected
    energy you get by integrating out the odd numbered layers and the
    expected energy you get by integrating out the even numbered layesr
    """

    def __init__(self, num_chains, num_gibbs_steps, supervised = False):
        """
        """
        self.__dict__.update(locals())
        del self.self
        self.theano_rng = MRG_RandomStreams(2012 + 10 + 14)
        assert supervised in [True, False]

    def __call__(self, model, X, Y=None):
        """
        The partition function makes this intractable.
        """

        if self.supervised:
            assert Y is not None

        return None

    def get_monitoring_channels(self, model, X, Y = None):
        rval = OrderedDict()

        history = model.mf(X, return_history = True)
        q = history[-1]

        if self.supervised:
            assert Y is not None
            Y_hat = q[-1]
            true = T.argmax(Y,axis=1)
            pred = T.argmax(Y_hat, axis=1)

            #true = Print('true')(true)
            #pred = Print('pred')(pred)

            wrong = T.neq(true, pred)
            err = T.cast(wrong.mean(), X.dtype)
            rval['misclass'] = err

            if len(model.hidden_layers) > 1:
                q = model.mf(X, Y = Y)
                pen = model.hidden_layers[-2].upward_state(q[-2])
                Y_recons = model.hidden_layers[-1].mf_update(state_below = pen)
                pred = T.argmax(Y_recons, axis=1)
                wrong = T.neq(true, pred)

                rval['recons_misclass'] = T.cast(wrong.mean(), X.dtype)


        return rval

    def get_gradients(self, model, X, Y=None):
        """
        PCD approximation to the gradient of the bound.
        Keep in mind this is a cost, so we are upper bounding
        the negative log likelihood.
        """

        if self.supervised:
            assert Y is not None
            # note: if the Y layer changes to something without linear energy,
            # we'll need to make the expected energy clamp Y in the positive phase
            assert isinstance(model.hidden_layers[-1], dbm.Softmax)



        q = model.mf(X, Y)


        """
            Use the non-negativity of the KL divergence to construct a lower bound
            on the log likelihood. We can drop all terms that are constant with
            repsect to the model parameters:

            log P(v) = L(v, q) + KL(q || P(h|v))
            L(v, q) = log P(v) - KL(q || P(h|v))
            L(v, q) = log P(v) - sum_h q(h) log q(h) + q(h) log P(h | v)
            L(v, q) = log P(v) + sum_h q(h) log P(h | v) + const
            L(v, q) = log P(v) + sum_h q(h) log P(h, v) - sum_h q(h) log P(v) + const
            L(v, q) = sum_h q(h) log P(h, v) + const
            L(v, q) = sum_h q(h) -E(h, v) - log Z + const

            so the cost we want to minimize is
            expected_energy + log Z + const


            Note: for the RBM, this bound is exact, since the KL divergence goes to 0.
        """

        variational_params = flatten(q)

        # The gradients of the expected energy under q are easy, we can just do that in theano
        expected_energy_q = model.expected_energy(X, q).mean()
        params = list(model.get_params())
        gradients = OrderedDict(safe_zip(params, T.grad(expected_energy_q, params,
            consider_constant = variational_params,
            disconnected_inputs = 'ignore')))

        """
        d/d theta log Z = (d/d theta Z) / Z
                        = (d/d theta sum_h sum_v exp(-E(v,h)) ) / Z
                        = (sum_h sum_v - exp(-E(v,h)) d/d theta E(v,h) ) / Z
                        = - sum_h sum_v P(v,h)  d/d theta E(v,h)
        """

        layer_to_chains = model.make_layer_to_state(self.num_chains)

        def recurse_check(l):
            if isinstance(l, (list, tuple)):
                for elem in l:
                    recurse_check(elem)
            else:
                assert l.get_value().shape[0] == self.num_chains

        recurse_check(layer_to_chains.values())

        model.layer_to_chains = layer_to_chains

        # Note that we replace layer_to_chains with a dict mapping to the new
        # state of the chains
        updates, layer_to_chains = model.get_sampling_updates(layer_to_chains,
                self.theano_rng, num_steps=self.num_gibbs_steps,
                return_layer_to_updated = True)



        # Variance reduction is hardcoded for this exact model
        assert isinstance(model.visible_layer, dbm.BinaryVector)
        assert isinstance(model.hidden_layers[0], dbm.BinaryVectorMaxPool)
        assert model.hidden_layers[0].pool_size == 1
        assert isinstance(model.hidden_layers[1], dbm.BinaryVectorMaxPool)
        assert model.hidden_layers[1].pool_size == 1
        assert isinstance(model.hidden_layers[2], dbm.Softmax)
        assert len(model.hidden_layers) == 3

        V_samples = layer_to_chains[model.visible_layer]
        H1_samples, H2_samples, Y_samples = [layer_to_chains[layer] for layer in model.hidden_layers]

        V_mf = model.visible_layer.inpaint_update(layer_above = model.hidden_layers[0],
                state_above = model.hidden_layers[0].downward_state(H1_samples))
        H1_mf = model.hidden_layers[0].mf_update(state_below=model.visible_layer.upward_state(V_samples),
                                                state_above=model.hidden_layers[1].downward_state(H2_samples),
                                                layer_above=model.hidden_layers[1])
        H2_mf = model.hidden_layers[1].mf_update(state_below=model.hidden_layers[0].upward_state(H1_samples),
                                                state_above=model.hidden_layers[2].downward_state(Y_samples),
                                                layer_above=model.hidden_layers[2])
        Y_mf = model.hidden_layers[2].mf_update(state_below=model.hidden_layers[1].upward_state(H2_samples))

        expected_energy_p = 0.5 * model.energy(V_samples, [H1_mf, H2_samples, Y_mf]).mean() + \
                            0.5 * model.energy(V_mf, [H1_samples, H2_mf, Y_samples]).mean()

        constants = flatten([V_samples, V_mf, H1_samples, H1_mf, H2_samples, H2_mf, Y_mf, Y_samples])

        neg_phase_grads = OrderedDict(safe_zip(params, T.grad(-expected_energy_p, params, consider_constant = constants)))


        for param in list(gradients.keys()):
            gradients[param] = neg_phase_grads[param] + gradients[param]

        return gradients, updates


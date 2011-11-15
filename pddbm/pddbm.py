__authors__ = "Ian Goodfellow"
__copyright__ = "Copyright 2011, Universite de Montreal"
__credits__ = ["Ian Goodfellow"]
__license__ = "3-clause BSD"
__maintainer__ = "Ian Goodfellow"

import time
from pylearn2.models.model import Model
from theano import config, function, shared
import theano.tensor as T
import numpy as np
import warnings
from theano.gof.op import get_debug_values, debug_error_message
from pylearn2.utils import make_name, as_floatX

warnings.warn('pddbm changing the recursion limit')
import sys
sys.setrecursionlimit(50000)

from pylearn2.models.s3c import numpy_norms
from pylearn2.models.s3c import theano_norms
from pylearn2.models.s3c import rotate_towards
from pylearn2.models.s3c import full_min
from pylearn2.models.s3c import full_max
from pylearn2.models.s3c import reflection_clip
from pylearn2.models.s3c import damp
from pylearn2.models.s3c import S3C

class SufficientStatistics:
    """ The SufficientStatistics class computes several sufficient
        statistics of a minibatch of examples / variational parameters.
        This is mostly for convenience since several expressions are
        easy to express in terms of these same sufficient statistics.
        Also, re-using the same expression for the sufficient statistics
        in multiple code locations can reduce theano compilation time.
        The current version of the S3C code no longer supports features
        like decaying sufficient statistics since these were not found
        to be particularly beneficial relative to the burden of computing
        the O(nhid^2) second moment matrix. The current version of the code
        merely computes the sufficient statistics apart from the second
        moment matrix as a notational convenience. Expressions that most
        naturally are expressed in terms of the second moment matrix
        are now written with a different order of operations that
        avoids O(nhid^2) operations but whose dependence on the dataset
        cannot be expressed in terms only of sufficient statistics."""


    def __init__(self, d):
        self. d = {}
        for key in d:
            self.d[key] = d[key]

    @classmethod
    def from_observations(self, needed_stats, V, H_hat, S_hat, var_s0_hat, var_s1_hat):
        """
            returns a SufficientStatistics

            needed_stats: a set of string names of the statistics to include

            V: a num_examples x nvis matrix of input examples
            H_hat: a num_examples x nhid matrix of \hat{h} variational parameters
            S_hat: variational parameters for expectation of s given h=1
            var_s0_hat: variational parameters for variance of s given h=0
                        (only a vector of length nhid, since this is the same for
                        all inputs)
            var_s1_hat: variational parameters for variance of s given h=1
                        (again, a vector of length nhid)
        """

        m = T.cast(V.shape[0],config.floatX)

        H_name = make_name(H_hat, 'anon_H_hat')
        Mu1_name = make_name(S_hat, 'anon_S_hat')

        #mean_h
        assert H_hat.dtype == config.floatX
        mean_h = T.mean(H_hat, axis=0)
        assert H_hat.dtype == mean_h.dtype
        assert mean_h.dtype == config.floatX
        mean_h.name = 'mean_h('+H_name+')'

        #mean_v
        mean_v = T.mean(V,axis=0)

        #mean_sq_v
        mean_sq_v = T.mean(T.sqr(V),axis=0)

        #mean_s1
        mean_s1 = T.mean(S_hat,axis=0)

        #mean_sq_s
        mean_sq_S = H_hat * (var_s1_hat + T.sqr(S_hat)) + (1. - H_hat)*(var_s0_hat)
        mean_sq_s = T.mean(mean_sq_S,axis=0)

        #mean_hs
        mean_HS = H_hat * S_hat
        mean_hs = T.mean(mean_HS,axis=0)
        mean_hs.name = 'mean_hs(%s,%s)' % (H_name, Mu1_name)
        mean_s = mean_hs #this here refers to the expectation of the s variable, not s_hat
        mean_D_sq_mean_Q_hs = T.mean(T.sqr(mean_HS), axis=0)

        #mean_sq_hs
        mean_sq_HS = H_hat * (var_s1_hat + T.sqr(S_hat))
        mean_sq_hs = T.mean(mean_sq_HS, axis=0)
        mean_sq_hs.name = 'mean_sq_hs(%s,%s)' % (H_name, Mu1_name)

        #mean_sq_mean_hs
        mean_sq_mean_hs = T.mean(T.sqr(mean_HS), axis=0)
        mean_sq_mean_hs.name = 'mean_sq_mean_hs(%s,%s)' % (H_name, Mu1_name)

        #mean_hsv
        sum_hsv = T.dot(mean_HS.T,V)
        sum_hsv.name = 'sum_hsv<from_observations>'
        mean_hsv = sum_hsv / m


        d = {
                    "mean_h"                :   mean_h,
                    "mean_v"                :   mean_v,
                    "mean_sq_v"             :   mean_sq_v,
                    "mean_s"                :   mean_s,
                    "mean_s1"               :   mean_s1,
                    "mean_sq_s"             :   mean_sq_s,
                    "mean_hs"               :   mean_hs,
                    "mean_sq_hs"            :   mean_sq_hs,
                    "mean_sq_mean_hs"       :   mean_sq_mean_hs,
                    "mean_hsv"              :   mean_hsv,
                }


        final_d = {}

        for stat in needed_stats:
            final_d[stat] = d[stat]
            final_d[stat].name = 'observed_'+stat

        return SufficientStatistics(final_d)


class PDDBM(Model):

    """ Implements a model of the form
        P(v,s,h,g[0],...,g[N_g]) = S3C(v,s|h)DBM(h,g)
    """

    def __init__(self,
            s3c,
            dbm,
            learning_rate,
            inference_procedure,
            print_interval = 10000):
        """
            s3c: a galatea.s3c.s3c.S3C object
                will become owned by the PDDBM
                it won't be deleted but many of its fields will change
            dbm: a pylearn2.dbm.DBM object
                will become owned by the PDDBM
                it won't be deleted but many of its fields will change
            inference_procedure: a galatea.pddbm.pddbm.InferenceProcedure
            h_bias_src: 's3c' to take bias on h from s3c, 'dbm' to take it from dbm
            print_interval: number of examples between each status printout
        """

        super(PDDBM,self).__init__()

        self.learning_rate = learning_rate

        self.s3c = s3c
        s3c.m_step = None
        self.dbm = dbm

        self.rng = np.random.RandomState([1,2,3])

        #must use DBM bias now
        self.s3c.bias_hid = self.dbm.bias_vis

        self.nvis = s3c.nvis

        #don't support some exotic options on s3c
        for option in ['monitor_functional',
                       'recycle_q',
                       'debug_m_step']:
            if getattr(s3c, option):
                warnings.warn('PDDBM does not support '+option+' in '
                        'the S3C layer, disabling it')
                setattr(s3c, option, False)

        self.print_interval = print_interval

        s3c.print_interval = None
        dbm.print_interval = None

        inference_procedure.register_model(self)
        self.inference_procedure = inference_procedure

        self.num_g = len(self.dbm.W)

        self.redo_everything()

    def redo_everything(self):

        #we don't call redo_everything on s3c because this would reset its weights
        #however, calling redo_everything on the dbm just resets its negative chain
        self.dbm.redo_everything()

        self.test_batch_size = 2

        self.redo_theano()


    def get_monitoring_channels(self, V):
            try:
                self.compile_mode()

                self.s3c.set_monitoring_channel_prefix('s3c_')

                rval = self.s3c.get_monitoring_channels(V, self)

                from_inference_procedure = self.inference_procedure.get_monitoring_channels(V, self)

                rval.update(from_inference_procedure)

                self.dbm.set_monitoring_channel_prefix('dbm_')

                from_dbm = self.dbm.get_monitoring_channels(V, self)

                rval.update(from_dbm)

            finally:
                self.deploy_mode()

    def compile_mode(self):
        """ If any shared variables need to have batch-size dependent sizes,
        sets them all to the sizes used for interactive debugging during graph construction """
        pass

    def deploy_mode(self):
        """ If any shared variables need to have batch-size dependent sizes, sets them all to their runtime sizes """
        pass

    def get_params(self):
        return list(set(self.s3c.get_params()).union(set(self.dbm.get_params())))

    def make_learn_func(self, V):
        """
        V: a symbolic design matrix
        """

        #run variational inference on the train set
        hidden_obs = self.inference_procedure.infer(V)

        #make a restricted dictionary containing only vars s3c knows about
        restricted_obs = {}
        for key in hidden_obs:
            if key != 'G_hat':
                restricted_obs[key] = hidden_obs[key]

        #request s3c sufficient statistics
        needed_stats = \
         S3C.expected_log_prob_v_given_hs_needed_stats().union(\
         S3C.log_likelihood_s_given_h_needed_stats())
        stats = SufficientStatistics.from_observations(needed_stats = needed_stats,
                V = V, **restricted_obs)

        G_hat = hidden_obs['G_hat']
        H_hat = hidden_obs['H_hat']
        S_hat = hidden_obs['S_hat']
        var_s0_hat = hidden_obs['var_s0_hat']
        var_s1_hat = hidden_obs['var_s1_hat']

        expected_log_prob_v_given_hs = self.s3c.expected_log_prob_v_given_hs(stats, \
                H_hat = H_hat, S_hat = S_hat)
        assert len(expected_log_prob_v_given_hs.type.broadcastable) == 0

        expected_log_prob_s_given_h  = self.s3c.log_likelihood_s_given_h(stats)
        assert len(expected_log_prob_s_given_h.type.broadcastable) == 0

        expected_dbm_energy = self.dbm.expected_energy( V_hat = H_hat, H_hat = G_hat )
        assert len(expected_dbm_energy.type.broadcastable) == 0

        entropy_g = self.dbm.entropy_h( H_hat = G_hat)
        assert len(entropy_g.type.broadcastable) == 1

        entropy_hs = self.s3c.entropy_hs( H_hat = H_hat, \
                var_s0_hat = var_s0_hat, var_s1_hat = var_s1_hat)
        assert len(entropy_hs.type.broadcastable) == 1

        entropy = T.mean(entropy_hs + entropy_g)
        assert len(entropy.type.broadcastable) == 0

        tractable_obj = expected_log_prob_v_given_hs + \
                        expected_log_prob_s_given_h  - \
                        expected_dbm_energy \
                        + entropy
        assert len(tractable_obj.type.broadcastable) == 0

        #don't backpropagate through inference
        obs_set = set(hidden_obs.values())
        stats_set = set(stats.d.values())
        constants = obs_set.union(stats_set)

        #take the gradient of the tractable part
        params = self.get_params()
        grads = T.grad(tractable_obj, params, consider_constant = constants)

        #put gradients into convenient dictionary
        params_to_grads = {}
        for param, grad in zip(params, grads):
            params_to_grads[param] = grad


        #add the approximate gradients
        params_to_approx_grads = self.dbm.get_neg_phase_grads()

        for param in params_to_approx_grads:
            params_to_grads[param] = params_to_grads[param] + params_to_approx_grads[param]


        learning_updates = self.get_param_updates(params_to_grads)

        sampling_updates = self.dbm.get_sampling_updates()

        for key in sampling_updates:
            learning_updates[key] = sampling_updates[key]

        self.censor_updates(learning_updates)

        print "compiling function..."
        t1 = time.time()
        rval = function([V], updates = learning_updates)
        t2 = time.time()
        print "... compilation took "+str(t2-t1)+" seconds"
        print "graph size: ",len(rval.maker.env.toposort())

        return rval

    def get_param_updates(self, params_to_grads):

        warnings.warn("TODO: get_param_updates does not use geodesics for now")

        rval = {}

        for key in params_to_grads:
            rval[key] = key + self.learning_rate * params_to_grads[key]

        return rval

    def censor_updates(self, updates):

        self.s3c.censor_updates(updates)
        self.dbm.censor_update(updates)

    def random_design_matrix(self, batch_size, theano_rng):

        if not hasattr(self,'p'):
            self.make_pseudoparams()

        H_sample = self.dbm.random_design_matrix(batch_size, theano_rng)

        V_sample = self.s3c.random_design_matrix(batch_size, theano_rng, H_sample = H_sample)

        return V_sample


    def make_pseudoparams(self):
        self.s3c.make_pseudoparams()

    def redo_theano(self):
        try:
            self.compile_mode()

            init_names = dir(self)

            self.make_pseudoparams()

            X = T.matrix(name='V')
            X.tag.test_value = np.cast[config.floatX](self.rng.randn(self.test_batch_size,self.nvis))

            self.learn_func = self.make_learn_func(X)

            final_names = dir(self)

            self.register_names_to_del([name for name in final_names if name not in init_names])
        finally:
            self.deploy_mode()
    #

    def learn(self, dataset, batch_size):
        self.learn_mini_batch(dataset.get_batch_design(batch_size))

    def learn_mini_batch(self, X):

        self.learn_func(X)

        if self.monitor.examples_seen % self.print_interval == 0:
            print ""
            print "S3C:"
            self.s3c.print_status()
            print "DBM:"
            self.dbm.print_status()

    def get_weights_format(self):
        return self.s3c.get_weights_format()

class InferenceProcedure:
    """

    Variational inference

    """

    def __init__(self, schedule,
                       clip_reflections = False,
                       monitor_kl = False,
                       rho = 0.5):
        """Parameters
        --------------
        schedule:
            list of steps. each step can consist of one of the following:
                ['s', <new_coeff>] where <new_coeff> is a number between 0. and 1.
                    does a damped parallel update of s, putting <new_coeff> on the new value of s
                ['h', <new_coeff>] where <new_coeff> is a number between 0. and 1.
                    does a damped parallel update of h, putting <new_coeff> on the new value of h
                ['g', idx]
                    does a block update of g[idx]

        clip_reflections, rho : if clip_reflections is true, the update to Mu1[i,j] is
            bounded on one side by - rho * Mu1[i,j] and unbounded on the other side
        """

        self.schedule = schedule

        self.clip_reflections = clip_reflections
        self.monitor_kl = monitor_kl

        self.rho = as_floatX(rho)

        self.model = None



    def get_monitoring_channels(self, V):

        rval = {}

        if self.monitor_kl:
            obs_history = self.infer(V, return_history = True)

            for i in xrange(1, 2 + len(self.h_new_coeff_schedule)):
                obs = obs_history[i-1]
                rval['trunc_KL_'+str(i)] = self.truncated_KL(V, obs).mean()

        return rval

    def register_model(self, model):
        self.model = model

        self.s3c_e_step = self.model.s3c.e_step

        self.s3c_e_step.clip_reflections = self.clip_reflections
        self.s3c_e_step.rho = self.rho

        self.dbm_ip = self.model.dbm.inference_procedure

    def truncated_KL(self, V, obs):
        """ KL divergence between variational and true posterior, dropping terms that don't
            depend on the variational parameters """

        s3c_truncated_KL = self.s3c_e_step.truncated_KL(self, V, obs)

        dbm_obs = self.dbm_observations(obs)

        #when computing the dbm truncated KL, we ignore the entropy on the visible units
        #and the visible bias term of the dbm energy function. otherwise these would both
        #get double-counted, since they are also part of s3c_truncated_KL
        dbm_truncated_KL = self.dbm_ip.truncated_KL(self,V, dbm_obs, ignore_vis = True)

        rval = s3c_truncated_KL + dbm_truncated_KL

        return rval

    def infer_H_hat(self, V, H_hat, S_hat, G1_hat):
        """
            G1_hat: variational parameters for the g layer closest to h
                    here we use the designation from the math rather than from
                    the list, where it is G_hat[0]
        """

        s3c_presigmoid = self.s3c_e_step.infer_H_hat_presigmoid(V, H_hat, S_hat)

        W = self.model.dbm.W[0]

        top_down = T.dot(G1_hat, W.T)

        presigmoid = s3c_presigmoid + top_down

        H = T.nnet.sigmoid(presigmoid)

        return H

    def infer(self, V, return_history = False):
        """

            return_history: if True:
                                returns a list of dictionaries with
                                showing the history of the variational
                                parameters
                                throughout fixed point updates
                            if False:
                                returns a dictionary containing the final
                                variational parameters
        """

        alpha = self.model.s3c.alpha
        s3c_e_step = self.s3c_e_step
        dbm_ip = self.dbm_ip

        var_s0_hat = 1. / alpha
        var_s1_hat = s3c_e_step.var_s1_hat()

        H_hat = s3c_e_step.init_H_hat(V)
        G_hat = dbm_ip.init_H_hat(H_hat)
        S_hat = s3c_e_step.init_S_hat(V)

        def check_H(my_H, my_V):
            if my_H.dtype != config.floatX:
                raise AssertionError('my_H.dtype should be config.floatX, but they are '
                        ' %s and %s, respectively' % (my_H.dtype, config.floatX))

            allowed_v_types = ['float32']

            if config.floatX == 'float64':
                allowed_v_types.append('float64')

            assert my_V.dtype in allowed_v_types

            if config.compute_test_value != 'off':
                from theano.gof.op import PureOp
                Hv = PureOp._get_test_value(my_H)

                Vv = my_V.tag.test_value

                assert Hv.shape[0] == Vv.shape[0]

        check_H(H_hat,V)

        def make_dict():

            return {
                    'G_hat' : tuple(G_hat),
                    'H_hat' : H_hat,
                    'S_hat' : S_hat,
                    'var_s0_hat' : var_s0_hat,
                    'var_s1_hat': var_s1_hat,
                    }

        history = [ make_dict() ]

        for step in self.schedule:

            letter, number = step

            if letter == 's':

                new_S_hat = s3c_e_step.infer_S_hat(V, H_hat, S_hat)

                if self.clip_reflections:
                    clipped_S_hat = reflection_clip(S_hat = S_hat, new_S_hat = new_S_hat, rho = self.rho)
                else:
                    clipped_S_hat = new_S_hat

                S_hat = damp(old = S_hat, new = clipped_S_hat, new_coeff = number)

            elif letter == 'h':

                new_H = self.infer_H_hat(V = V, H_hat = H_hat, S_hat = S_hat, G1_hat = G_hat[0])

                H_hat = damp(old = H_hat, new = new_H, new_coeff = number)

                check_H(H_hat,V)

            elif letter == 'g':

                if not isinstance(number, int):
                    raise ValueError("Step parameter for 'g' code must be an integer in [0, # g layers) "
                            "but got "+str(number)+" (of type "+str(type(number)))

                b = self.model.dbm.bias_hid[number]

                W = self.model.dbm.W

                W_below = W[number]

                if number == 0:
                    H_hat_below = H_hat
                else:
                    H_hat_below = G_hat[number - 1]

                num_g = self.model.num_g

                if number == num_g - 1:
                    G_hat[number] = dbm_ip.infer_H_hat_one_sided(other_H_hat = H_hat_below, W = W_below, b = b)
                else:
                    H_hat_above = G_hat[number + 1]
                    W_above = W[number+1]
                    G_hat[number] = dbm_ip.infer_H_hat_two_sided(H_hat_below = H_hat_below, H_hat_above = H_hat_above,
                                           W_below = W_below, W_above = W_above,
                                           b = b)
            else:
                raise ValueError("Invalid inference step code '"+letter+"'. Valid options are 's','h' and 'g'.")

            history.append(make_dict())

        if return_history:
            return history
        else:
            return history[-1]


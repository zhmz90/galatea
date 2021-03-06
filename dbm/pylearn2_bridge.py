"""
By making pylearn2 call run_unit_tests, I can have pylearn2
unit test stuff that I am still keeping private.
"""

import numpy as np

from theano import config
from theano import function
floatX = config.floatX

from galatea.dbm.inpaint.super_dbm import BiasInit
from galatea.dbm.inpaint.super_dbm import BinaryVector
from galatea.dbm.inpaint.super_dbm import BinaryVectorMaxPool
from galatea.dbm.inpaint.super_dbm import MoreConsistent
from galatea.dbm.inpaint.super_dbm import MoreConsistent2
from galatea.dbm.inpaint.super_dbm import Softmax
from pylearn2.utils import sharedX

from galatea.dbm.inpaint.super_dbm import SuperDBM

def run_unit_tests():
    test_mean_field_matches_inpainting(BiasInit)
    test_mean_field_matches_inpainting(MoreConsistent)
    test_mean_field_matches_inpainting(MoreConsistent2)
    print 'All passed'

def test_mean_field_matches_inpainting(cls):

    # Tests that running mean field to infer Y given X
    # gives the same result as running inpainting with
    # Y masked out and all of X observed

    # This test uses the MoreConsistent InferenceProcedure
    # For SuperWeightDoubling it will fail because mf initializes Y_hat
    # with double weights and do_inpainting initializes it with sigmoid(biases)

    batch_size = 5
    niter = 3
    nvis = 4
    nhid1 = 5
    nhid2 = 10
    classes = 3

    rng = np.random.RandomState([2012,11,3])

    vis = BinaryVector(nvis)
    vis.set_biases( rng.randn(nvis).astype(floatX) )

    h1 = BinaryVectorMaxPool(
        detector_layer_dim=nhid1,
        pool_size=1,
        layer_name='h1',
        irange=1.
            )

    h2 = BinaryVectorMaxPool(
        detector_layer_dim=nhid2,
        pool_size=1,
        layer_name='h2',
        irange=1.
            )

    y = Softmax(n_classes = classes,
            irange=1., layer_name = 'y')

    dbm = SuperDBM(batch_size=batch_size,
            inference_procedure = cls(),
            niter=niter,
            visible_layer=vis,
            hidden_layers=[h1, h2, y])

    X = sharedX(rng.randn(batch_size, nvis))
    Y = sharedX(np.zeros((batch_size, classes)))
    drop_mask = sharedX(np.zeros((batch_size, nvis)))
    drop_mask_Y = sharedX(np.ones((batch_size,)))

    q_mf = dbm.mf(X)
    Y_hat_mf = q_mf[-1]

    V_hat_inpaint, Y_hat_inpaint = dbm.do_inpainting(V=X, Y=Y,
            drop_mask=drop_mask, drop_mask_Y=drop_mask_Y)

    Y_hat_mf, Y_hat_inpaint = function([], [Y_hat_mf, Y_hat_inpaint])()

    if not np.allclose(Y_hat_mf, Y_hat_inpaint):
        print Y_hat_mf
        print Y_hat_inpaint
        assert False, cls

if __name__ == '__main__':
    run_unit_tests()

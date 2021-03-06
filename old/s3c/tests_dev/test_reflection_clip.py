import numpy as np
from galatea.s3c.s3c import reflection_clip
from theano import function
from theano import shared

def test_reflection_clip():
    N = 5
    m = 10

    rng = np.random.RandomState([1,2,3])

    Mu1_old = rng.randn(m,N)
    Mu1_new = rng.randn(m,N)

    rho = .6

    Mu1_clipped = function([],reflection_clip( \
            shared(Mu1_old), shared(Mu1_new), rho))()

    case1 = False
    case2 = False
    case3 = False
    case4 = False

    for i in xrange(m):
        for j in xrange(N):
            old = Mu1_old[i,j]
            new = Mu1_new[i,j]
            clipped = Mu1_clipped[i,j]

            if old > 0.:
                if new < - rho * old:
                    case1 = True
                    assert abs(clipped-(-rho*old)) < 1e-6
                else:
                    case2 = True
                    assert new == clipped
            elif old < 0.:
                if new > - rho * old:
                    case3 = True
                    assert abs(clipped-(-rho*old)) < 1e-6
                else:
                    case4 = True
                    assert new == clipped
            else:
                assert new == clipped

    #if any of the following fail, it doesn't necessarily mean
    #that reflection_clip is broken, just that the test needs
    #to be adjusted to have better coverage
    assert case1
    assert case2
    assert case3
    assert case4

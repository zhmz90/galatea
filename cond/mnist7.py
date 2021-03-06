#this tests only on the central pixels of the image
#each traning example is translated randomly by a few pixels
#originally this translated the whole training set in the same way at
#the start of each epoch, that didn't work well
#now it translates each example individually
#compare to mnist6 which trains and tests only on the central pixels
#this results in
#doing epoch 20850
#        TRAIN acc [ 0.99993333]
#        transformation time:  0.845413923264
#                terminal velocity multiplier: 10.0
#                        learning rate: 0.0124320078995
#                                test acc:  [array(0.9862)]


from pylearn2.utils import sharedX
from pylearn2.datasets.mnist import MNIST
from theano.printing import Print
import time
import numpy as np
from cDBM import cDBM
import sys
name = sys.argv[0].replace('.py','')

remove_border = 3
irange1 = .05
irange2 = .05
irange3 = .05
nvis = (28-2*remove_border)**2
nclass = 10
nhid1 = 500
nhid2 = 500
mf_iter = 10
batch_size = 100
lr = .1
lr_decay = 1.0001
min_lr = 5e-5
init_tv = 2.
tv_mult = 1.001
max_tv = 10.
l1wd = 0.
l2wd = 0.
l3wd = 0.
sp_coeff = .000
sp_targ = .1

dataset = MNIST(which_set = 'train', one_hot = True)
base_topo = dataset.get_topological_view()

transformed_dataset = MNIST(which_set = 'train', one_hot = True)
transformed_topo = base_topo[:,remove_border:-remove_border,remove_border:-remove_border,:].copy()
transformed_dataset.set_topological_view(transformed_topo)
transformed_topo

X = sharedX(transformed_dataset.X)

rng = np.random.RandomState([2012,7,24])



W1 = rng.uniform(-irange1,irange1,(nvis,nhid1))
b1 = np.zeros((nhid1,))-1.
W2 = rng.uniform(-irange2,irange2,(nhid1,nhid2))
b2 = np.zeros((nhid2,))-1.
W3 = rng.uniform(-irange3,irange3,(nhid2,nclass))
b3 = np.zeros((nclass,))

def transform():
    #don't call this until after the weights are chosen
    #so the random numbers used to generate the weights
    #are the same as in mnist6
    t1 = time.time()
    for i in xrange(m):
        row_start = rng.randint(0,remove_border+1)
        col_start = rng.randint(0,remove_border+1)
        transformed_topo[i,:,:,:] = \
            base_topo[i,row_start:row_start+28-2*remove_border,
                       col_start:col_start+28-2*remove_border,
                       :]
    transformed_dataset.set_topological_view(transformed_topo)
    X.set_value(transformed_dataset.X)
    t2 = time.time()
    print 'transformation time: ',t2-t1

import theano.tensor as T

m = dataset.X.shape[0]

y = sharedX(dataset.y)

idx = T.iscalar()
idx.tag.test_value = 0

Xb = X[idx*batch_size:(idx+1)*batch_size,:]
yb = y[idx*batch_size:(idx+1)*batch_size,:]


mf1mod = cDBM(W1,b1,W2,b2, W3, b3,  mf_iter = 1)

ymf1_arg = mf1mod.mf1y_arg(Xb)

def log_p_yb(y_arg):
    assert y_arg.ndim == 2
    mx = y_arg.max(axis=1)
    assert mx.ndim == 1
    y_arg = y_arg - mx.dimshuffle(0,'x')
    assert yb.ndim == 2
    example_costs =  (yb * y_arg).sum(axis=1) - T.log(T.exp(y_arg).sum(axis=1))

    #log P(y = i) = log exp( arg_i ) / sum_j exp( arg_j)
    #           = arg_i - log sum_j exp(arg_j)
    return example_costs.mean()

confidence = ymf1_arg - ((1-yb)*ymf1_arg).max(axis=1).dimshuffle(0,'x')
misclass_cost = - (confidence * yb).sum(axis=1).mean()


mf1_cost = - log_p_yb ( ymf1_arg) + \
             l1wd * T.sqr(mf1mod.W1).sum() +\
             l2wd * T.sqr(mf1mod.W2).sum() +\
             l3wd * T.sqr(mf1mod.W3).sum()

updates = {}

alpha = T.scalar()
alpha.tag.test_value = 1e-4

tv = T.scalar()
momentum = 1. - 1. / tv

for cost, params in [ (mf1_cost, mf1mod.params())  ]:
    for param in params:
        inc = sharedX(np.zeros(param.get_value().shape))
        updates[inc] = momentum * inc - alpha * T.grad(cost,param)
        updates[param] = param + updates[inc]

from theano import function

func = function([idx,alpha,tv],[mf1_cost],updates = updates)

dataset = MNIST(which_set = 'test', one_hot = True)
topo = dataset.get_topological_view()
topo = topo[:,remove_border:-remove_border,remove_border:-remove_border,:]
dataset.set_topological_view(topo)

Xt = sharedX(dataset.X)
yt = sharedX(dataset.y)

mf1yt = mf1mod.mf1y(Xt)

ytl = T.argmax(yt,axis=1)

mf1acc = 1.-T.neq(ytl , T.argmax(mf1yt,axis=1)).mean()

accs = function([],[mf1acc])

mf1yb = mf1mod.mf1y(Xb)

ybl = T.argmax(yb,axis=1)

mf1acc = 1.-T.neq(ybl , T.argmax(mf1yb,axis=1)).mean()

baccs = function([idx],[mf1acc])


def taccs():
    result = np.zeros((m/batch_size,1))
    for i in xrange(m/batch_size):
        result[i,:] = baccs(i)
    return result.mean(axis=0)

from pylearn2.utils import serial

alpha = lr
epoch = 0
tv = init_tv
while True:
    epoch += 1
    print '\tterminal velocity multiplier:',tv
    print '\tlearning rate:',alpha
    print '\ttest acc: ',accs()
    print 'doing epoch',epoch
    for i in xrange(m/batch_size):
        mf1_cost = func(i,alpha,tv)
    alpha = max(alpha/lr_decay,min_lr)
    tv = min(tv*tv_mult,max_tv)
    if epoch % 10 == 0:
        serial.save('mf1_model_%s.pkl' % name,mf1mod)
        print '\tTRAIN acc',taccs()
    transform()
        #print mf1_cost, mfn_cost

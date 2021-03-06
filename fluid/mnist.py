from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import argparse
import time

import paddle.v2 as paddle
import paddle.v2.fluid as fluid
import paddle.v2.fluid.core as core
import paddle.v2.fluid.profiler as profiler

SEED = 1
DTYPE = "float32"
# random seed must set before configuring the network.
fluid.default_startup_program().random_seed = SEED


def parse_args():
    parser = argparse.ArgumentParser("mnist model benchmark.")
    parser.add_argument(
        '--batch_size', type=int, default=128, help='The minibatch size.')
    parser.add_argument(
        '--iterations', type=int, default=35, help='The number of minibatches.')
    parser.add_argument(
        '--pass_num', type=int, default=5, help='The number of passes.')
    parser.add_argument(
        '--device',
        type=str,
        default='GPU',
        choices=['CPU', 'GPU'],
        help='The device type.')
    parser.add_argument(
        '--infer_only', action='store_true', help='If set, run forward only.')
    parser.add_argument(
        '--use_cprof', action='store_true', help='If set, use cProfile.')
    parser.add_argument(
        '--use_nvprof',
        action='store_true',
        help='If set, use nvprof for CUDA.')
    args = parser.parse_args()
    return args


def print_arguments(args):
    vars(args)['use_nvprof'] = (vars(args)['use_nvprof'] and
                                vars(args)['device'] == 'GPU')
    print('-----------  Configuration Arguments -----------')
    for arg, value in sorted(vars(args).iteritems()):
        print('%s: %s' % (arg, value))
    print('------------------------------------------------')


def cnn_model(data):
    conv_pool_1 = fluid.nets.simple_img_conv_pool(
        input=data,
        filter_size=5,
        num_filters=20,
        pool_size=2,
        pool_stride=2,
        act="relu")
    conv_pool_2 = fluid.nets.simple_img_conv_pool(
        input=conv_pool_1,
        filter_size=5,
        num_filters=50,
        pool_size=2,
        pool_stride=2,
        act="relu")

    # TODO(dzhwinter) : refine the initializer and random seed settting
    SIZE = 10
    input_shape = conv_pool_2.shape
    param_shape = [reduce(lambda a, b: a * b, input_shape[1:], 1)] + [SIZE]
    scale = (2.0 / (param_shape[0]**2 * SIZE))**0.5

    predict = fluid.layers.fc(
        input=conv_pool_2,
        size=SIZE,
        act="softmax",
        param_attr=fluid.param_attr.ParamAttr(
            initializer=fluid.initializer.NormalInitializer(
                loc=0.0, scale=scale)))
    return predict


def eval_test(exe, accuracy, avg_cost):
    test_reader = paddle.batch(
        paddle.dataset.mnist.test(), batch_size=args.batch_size)
    accuracy.reset(exe)
    for batch_id, data in enumerate(test_reader()):
        img_data = np.array(map(lambda x: x[0].reshape([1, 28, 28]),
                                data)).astype(DTYPE)
        y_data = np.array(map(lambda x: x[1], data)).astype("int64")
        y_data = y_data.reshape([len(y_data), 1])

        exe.run(fluid.default_main_program(),
                feed={"pixel": img_data,
                      "label": y_data},
                fetch_list=[avg_cost] + accuracy.metrics)

    pass_acc = accuracy.eval(exe)
    return pass_acc


def run_benchmark(model, args):
    if args.use_cprof:
        pr = cProfile.Profile()
        pr.enable()
    start_time = time.time()
    images = fluid.layers.data(name='pixel', shape=[1, 28, 28], dtype=DTYPE)
    label = fluid.layers.data(name='label', shape=[1], dtype='int64')
    predict = model(images)

    cost = fluid.layers.cross_entropy(input=predict, label=label)
    avg_cost = fluid.layers.mean(x=cost)
    opt = fluid.optimizer.AdamOptimizer(
        learning_rate=0.001, beta1=0.9, beta2=0.999)
    opt.minimize(avg_cost)

    accuracy = fluid.evaluator.Accuracy(input=predict, label=label)

    train_reader = paddle.batch(
        paddle.dataset.mnist.train(), batch_size=args.batch_size)

    place = core.CPUPlace()
    exe = fluid.Executor(place)

    exe.run(fluid.default_startup_program())

    for pass_id in range(args.pass_num):
        accuracy.reset(exe)
        pass_start = time.clock()
        for batch_id, data in enumerate(train_reader()):
            img_data = np.array(
                map(lambda x: x[0].reshape([1, 28, 28]), data)).astype(DTYPE)
            y_data = np.array(map(lambda x: x[1], data)).astype("int64")
            y_data = y_data.reshape([len(y_data), 1])

            start = time.clock()
            outs = exe.run(fluid.default_main_program(),
                           feed={"pixel": img_data,
                                 "label": y_data},
                           fetch_list=[avg_cost] + accuracy.metrics)
            end = time.clock()
            loss = np.array(outs[0])
            acc = np.array(outs[1])
            print("pass=%d, batch=%d, loss=%f, error=%f, elapse=%f" %
                  (pass_id, batch_id, loss, 1 - acc, (end - start) / 1000))

        pass_end = time.clock()
        test_avg_acc = eval_test(exe, accuracy, avg_cost)
        pass_acc = accuracy.eval(exe)
        print("pass=%d, test_avg_acc=%f, test_avg_acc=%f, elapse=%f" %
              (pass_id, pass_acc, test_avg_acc, (pass_end - pass_start) / 1000))


if __name__ == '__main__':
    args = parse_args()
    print_arguments(args)
    if args.use_nvprof and args.device == 'GPU':
        with profiler.cuda_profiler("cuda_profiler.txt", 'csv') as nvprof:
            run_benchmark(cnn_model, args)
    else:
        run_benchmark(cnn_model, args)

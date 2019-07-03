# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import numpy as np
import tvm
from tvm import relay
from tvm.relay.analysis import alpha_equal, detect_feature
from tvm.relay.transform import to_cps, un_cps
from tvm.relay.feature import Feature
from tvm.relay.prelude import Prelude
from tvm.relay.testing import add_nat_definitions, make_nat_expr, run_infer_type, run_opt_pass
from tvm.relay import create_executor
from tvm.relay import Function, transform


def rand(dtype='float32', *shape):
    return tvm.nd.array(np.random.rand(*shape).astype(dtype))


# make sure cps work for recursion.
def test_recursion():
    mod = relay.Module()
    p = Prelude(mod)
    add_nat_definitions(p)
    shape = (10, 10)
    dtype = 'float32'
    t = relay.TensorType(shape, dtype)
    x = relay.var("x", t)
    double = relay.Function([x], x + x)
    i = relay.var("i", t)
    func = relay.Function([i], p.nat_iterate(double, make_nat_expr(p, 3))(i))
    mod[mod.entry_func] = func
    mod[mod.entry_func] = to_cps(mod[mod.entry_func], mod=mod)
    mod[mod.entry_func] = un_cps(mod[mod.entry_func])
    ex = create_executor(mod=mod)
    i_nd = rand(dtype, *shape)
    forward = ex.evaluate(mod.entry_func)(i_nd)
    tvm.testing.assert_allclose(forward.asnumpy(), 8 * i_nd.asnumpy())


# This serve as an integration test.
# It test that, given a program with reference,
# cps and pe can completely eliminate the allocation of reference.
def test_cps_pe():
    def destroy_ref(x):
        x = run_infer_type(x)
        x = to_cps(x)
        x = run_infer_type(x)
        y = un_cps(x)
        y = run_infer_type(y)
        x = run_opt_pass(x, transform.Sequential([transform.PartialEvaluate(), transform.DeadCodeElimination(inline_once=True)]))
        assert Feature.fRefCreate not in detect_feature(x)
    unit = relay.Function([], relay.const(0., dtype='float32'))
    f_ref = relay.Var("f_ref")

    one = relay.const(1., dtype='float32')
    two = relay.const(2., dtype='float32')
    cond = relay.var(shape=(), dtype='uint1', name_hint='cond')
    true_branch = relay.RefWrite(f_ref, relay.Function([], one))
    false_branch = relay.RefWrite(f_ref, relay.Function([], two))
    if_expr = relay.If(cond, true_branch, false_branch)

    stmt = relay.Let(f_ref, relay.RefCreate(unit),
                     relay.Let(relay.Var("x"), if_expr,
                               relay.Call(relay.RefRead(f_ref), [])))

    F = relay.Function([cond], stmt)
    destroy_ref(F)

    G = relay.Function([cond], relay.If(cond, one, two))
    G = relay.transform.gradient(G)
    destroy_ref(G)

    x = relay.var("x", shape=(1, 16))
    y = relay.var("y", shape=(1, 16))
    z = relay.var("z", shape=(1, 16))
    cond = relay.var("cond", shape=(), dtype='uint1')
    H = relay.If(cond, x, y)
    H = relay.add(H, z)
    H = relay.Function([cond,x,y,z], H)
    H = relay.transform.gradient(H)
    destroy_ref(H)


if __name__ == '__main__':
    test_recursion()
    test_cps_pe()

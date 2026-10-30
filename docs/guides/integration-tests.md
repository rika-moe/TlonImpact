# 集成测试的成员范围

普通进程内集成测试默认不播种初始 30 人，由用例创建自己需要的成员。测试仍使用真实服务、HTTP、SQLite、成员执行和重启流程；原型库与技能正常播种，每个用例保留独立数据目录。

该策略仅由 `src/tests/integration/conftest.py` 的 function-scope fixture 实现，不修改产品启动行为，不增加环境变量或运行配置。fixture 仅替换初始社会播种函数，其他装配流程保持真实。

验证初始社会本身的用例必须标记 `@pytest.mark.full_initial_society`。初始社会、首次模型配置和冻结便携包验收保留完整 30 人与原有断言。真实 `tlon dream` 子进程不受进程内 fixture 影响，继续覆盖完整 CLI 启动和退出。

普通用例需要验证重启后保留的成员时，应明确创建对应成员，不能把原有非空断言改成空集合断言；例如成员目录用例创建一位保留成员和一位随后退休的成员。

验证入口仍为 `scripts/check_all.sh --integration`，完整质量门为 `scripts/check_all.sh`。比较耗时时使用相同环境，并同时核对通过用例数与完整社会用例的结果。

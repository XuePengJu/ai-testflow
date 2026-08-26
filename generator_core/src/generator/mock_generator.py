"""本地规则生成（mock 模式）：未配置百炼 Key 时，也能产出结构化、可评审的用例。"""
from src.models.testcase import TestCase, CaseType, Priority, RequirementUnit


def mock_generate(unit: RequirementUnit) -> list[TestCase]:
    """根据测试单元类型（api / module）生成覆盖 正向/异常/边界/场景 的用例。"""
    cases: list[TestCase] = []
    name = unit.name

    if unit.kind == "api":
        module = "订单对接API"
        cases += [
            TestCase(title=f"[{name}] 合法请求-正向下单", module=module, case_type=CaseType.POSITIVE, priority=Priority.P0,
                     pre_condition="后台已创建 App 且 appState=1，secret 正确",
                     steps=["构造合法 dataStr（订单 JSON）", "计算 sign=md5(dataStr+secret)", "POST /other-api 携带 appId/action/dataStr/sign"],
                     expected="返回 code=200 status=success，ERP 生成对应订单", test_data="正常订单参数"),
            TestCase(title=f"[{name}] 非法 appId-应用不存在/未启用", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     pre_condition="使用一个不存在或未启用的 appId",
                     steps=["携带错误 appId 发送请求"], expected="返回 code=404，提示账户不存在，不落地订单", test_data="appId=invalid"),
            TestCase(title=f"[{name}] sign 签名篡改-校验失败", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     pre_condition="正常 appId，但 dataStr 被篡改后未重算 sign",
                     steps=["修改 dataStr 后不重新计算 sign", "发送请求"], expected="返回 sign 不一致，拒绝处理", test_data="sign 错误"),
            TestCase(title=f"[{name}] 必填缺失-appId 为空", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["不传 appId 发送请求"], expected="参数校验失败，返回错误", test_data="appId 空"),
            TestCase(title=f"[{name}] 不合法 action-拒绝执行", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["action 传入不存在的动作名"], expected="拒绝执行，返回错误", test_data="action=unknown"),
            TestCase(title=f"[{name}] 边界-购买数量为 0/负数", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P2,
                     steps=["goodsBuyNum 传 0 或 -1"], expected="校验失败或被拦截", test_data="goodsBuyNum=-1"),
            TestCase(title=f"[{name}] 边界-超长字符串字段", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P2,
                     steps=["buyName 传 1000 字符"], expected="长度校验或被截断处理", test_data="超长输入"),
            TestCase(title=f"[{name}] 场景-下单→支付→发货→完成 状态机", module=module, case_type=CaseType.SCENARIO, priority=Priority.P0,
                     pre_condition="订单已通过接口写入 ERP",
                     steps=["otherAddOrder 下单", "otherPaymentOrder 支付", "otherDeliverOrder 发货", "otherFinishOrder 完成"],
                     expected="状态严格按序流转，最终订单完成且库存/财务联动正确", test_data="全链路"),
            TestCase(title=f"[{name}] 场景-重复 orderSn 幂等", module=module, case_type=CaseType.SCENARIO, priority=Priority.P1,
                     steps=["用相同 orderSn 重复调用 otherAddOrder 两次"], expected="第二次不重复生成 ERP 订单（幂等）", test_data="重复 orderSn"),
        ]
    else:  # module（业务需求）
        module = name
        cases += [
            TestCase(title=f"[{module}] 主流程-正向操作", module=module, case_type=CaseType.POSITIVE, priority=Priority.P0,
                     pre_condition="登录具备该模块权限的账号",
                     steps=["进入模块", "完成核心正向操作"], expected="操作成功，数据正确落库", test_data="正常数据"),
            TestCase(title=f"[{module}] 必填项缺失-提交拦截", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["关键字段留空提交"], expected="前后端校验拦截，提示必填", test_data="空字段"),
            TestCase(title=f"[{module}] 越权访问-无权限角色", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     pre_condition="使用无该模块权限的账号",
                     steps=["直接访问模块 URL / 操作"], expected="被拦截或提示无权限", test_data="越权账号"),
            TestCase(title=f"[{module}] 业务规则-库存不为负", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P0,
                     steps=["出库数量大于当前可用库存"], expected="拦截，不允许负库存", test_data="超库存出库"),
            TestCase(title=f"[{module}] 业务规则-金额对账一致", module=module, case_type=CaseType.SCENARIO, priority=Priority.P1,
                     steps=["完成一笔业务", "核对库存/财务/报表三方数据"], expected="数量与金额三方一致", test_data="对账"),
            TestCase(title=f"[{module}] 边界-金额精度/超大值", module=module, case_type=CaseType.BOUNDARY, priority=Priority.P2,
                     steps=["金额输入超长小数或超大值"], expected="精度控制或拦截", test_data="金额边界"),
            TestCase(title=f"[{module}] 状态机-非法流转", module=module, case_type=CaseType.NEGATIVE, priority=Priority.P1,
                     steps=["跳过中间状态直接完成/回退"], expected="状态机拒绝非法跳转", test_data="状态越级"),
        ]
    return cases

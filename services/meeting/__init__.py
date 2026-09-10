"""Meeting Room API：第三个被测对象，验证时间冲突类业务规则的框架适配。

与 Mini Shop（数量/余额副作用）和 Library（副本/押金副作用）不同，
Meeting 的核心不变量是时间窗不重叠：同一会议室在 [start, end) 区间内
不允许存在两笔 booked 预约。
"""

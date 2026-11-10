"""Library API：第二个被测对象，用于 V2 框架的二次验证。

领域与 Mini Shop 刻意不同（图书借阅 vs 电商下单），但保留可比对的结构：
认证 + CRUD + 状态机（borrowed -> returned）+ 副本/押金副作用。
"""

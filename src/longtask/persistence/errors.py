"""持久层错误类（DESIGN §3.1、§7、§11.3）。

所有 SQLite 写操作抛的异常都在这里集中声明；调用方按子类区分原因
（fencing / revision 冲突 / 幂等不一致 / 篡改）。无业务依赖。
"""

from __future__ import annotations


class StoreError(Exception):
    """持久层错误基类（DESIGN §3.1）。"""


class StoreTamperedError(StoreError):
    """发现外部写入痕迹或 schema 版本不支持（DESIGN §3.1 人类编辑门、§13.3）。"""


class LeaseCASError(StoreError):
    """租约 CAS 期望 generation 不符（DESIGN §7）。"""


LeaseConflictError = LeaseCASError


class LeaseFencedError(StoreError):
    """写回携带过期 lease_generation 或 attempt 不符（DESIGN §7、§11.3、§14.1）。"""


class RevisionConflictError(StoreError):
    """合同修订版本冲突（DESIGN §11.2、§11.7）。"""


class IdempotencyMismatchError(StoreError):
    """同一 request_id 重放但入参不一致（DESIGN §11.3、§11.7）。"""

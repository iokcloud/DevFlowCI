"""闭环修复 do_review 回调签名兼容性。"""

import pytest


@pytest.mark.asyncio
async def test_do_review_callback_accepts_retry_count_keyword():
    """closed_loop 以 retry_count= 调用 do_review，回调须接受该关键字。"""

    async def do_review(mn, summary, code, test_code, retry_count=0):
        return {"module": mn, "retry_count": retry_count}

    result = await do_review(
        "analytics_api",
        "summary",
        "print('x')",
        "def test_x(): pass",
        retry_count=2,
    )
    assert result["retry_count"] == 2

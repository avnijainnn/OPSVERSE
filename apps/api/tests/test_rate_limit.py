from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.live_rate_limit import check_analysis_budget


def test_expensive_action_budget_is_scoped_to_user_workspace_and_action():
    user = uuid4().hex
    workspace = uuid4().hex
    check_analysis_budget(user, workspace, "compare", max_calls=2)
    check_analysis_budget(user, workspace, "compare", max_calls=2)
    with pytest.raises(HTTPException) as error:
        check_analysis_budget(user, workspace, "compare", max_calls=2)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"]
    check_analysis_budget(user, workspace, "backtest", max_calls=2)
    check_analysis_budget(user, uuid4().hex, "compare", max_calls=2)

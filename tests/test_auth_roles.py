from jose import jwt

from auth import (
    ACCESS_TOKEN_TYPE,
    ALGORITHM,
    REFRESH_TOKEN_TYPE,
    SECRET_KEY,
    create_access_token,
    create_refresh_token,
)
from models import User, UserRole
from schemas import UserResponse


def test_user_response_exposes_role_name_from_relationship():
    role = UserRole(id=4, role_name="domu_editor", can_access_internal_docs=True)
    user = User(
        id=42,
        email="domu@example.test",
        username="domu",
        is_active=True,
    )
    user.role = role

    response = UserResponse.model_validate(user)

    assert response.role == "domu_editor"
    assert response.can_access_internal_docs is True


def test_access_and_refresh_tokens_have_distinct_types():
    access_token = create_access_token({"sub": "admin@example.test"})
    refresh_token = create_refresh_token({"sub": "admin@example.test"})

    access_payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
    refresh_payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])

    assert access_payload["type"] == ACCESS_TOKEN_TYPE
    assert refresh_payload["type"] == REFRESH_TOKEN_TYPE

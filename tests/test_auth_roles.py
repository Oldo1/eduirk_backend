from models import User, UserRole
from schemas import UserResponse


def test_user_response_exposes_role_name_from_relationship():
    role = UserRole(id=4, role_name="domu_editor")
    user = User(
        id=42,
        email="domu@example.test",
        username="domu",
        full_name="Соколова Елена Павловна",
        is_active=True,
    )
    user.role = role

    response = UserResponse.model_validate(user)

    assert response.role == "domu_editor"
    assert response.full_name == "Соколова Елена Павловна"

from assistant_access import (
    EMPLOYEE_SCOPE,
    INTERNAL_ACCESS,
    PUBLIC_ACCESS,
    PUBLIC_SCOPE,
    access_scope_for_role,
    can_access_document,
    document_access_level,
    find_s3_folder_conflicts,
    infer_s3_access_level,
    scoped_session_id,
)
from langchain_core.documents import Document
from rag_pipeline import RAGSystem


def test_employee_roles_get_employee_scope():
    assert access_scope_for_role("admin") == EMPLOYEE_SCOPE
    assert access_scope_for_role("Сотрудник") == EMPLOYEE_SCOPE
    assert access_scope_for_role("Администратор МКУ РОИ") == EMPLOYEE_SCOPE
    assert access_scope_for_role(None) == PUBLIC_SCOPE


def test_internal_s3_keys_are_restricted():
    assert infer_s3_access_level("internal/report.docx") == INTERNAL_ACCESS
    assert infer_s3_access_level("Внутренние/регламент.pdf") == INTERNAL_ACCESS
    assert infer_s3_access_level("materials/public.docx") == PUBLIC_ACCESS


def test_document_access_defaults_to_public_without_metadata():
    assert document_access_level({}) == PUBLIC_ACCESS
    assert can_access_document({}, PUBLIC_SCOPE) is True


def test_public_scope_cannot_access_internal_document():
    metadata = {"access_level": INTERNAL_ACCESS, "s3_key": "internal/report.docx"}

    assert can_access_document(metadata, PUBLIC_SCOPE) is False
    assert can_access_document(metadata, EMPLOYEE_SCOPE) is True


def test_session_id_is_scoped_by_user_and_access_scope():
    public_session = scoped_session_id("default", PUBLIC_SCOPE, None)
    employee_session = scoped_session_id("default", EMPLOYEE_SCOPE, 7)

    assert public_session == "public:anonymous:default"
    assert employee_session == "employee:7:default"
    assert public_session != employee_session


def test_find_s3_folder_conflicts_reports_filename_and_keys():
    conflicts = find_s3_folder_conflicts([
        "public/report.docx",
        "internal/report.docx",
        "public/unique.pdf",
    ])

    assert len(conflicts) == 1
    assert conflicts[0]["filename"] == "report.docx"
    assert conflicts[0]["keys"] == ["internal/report.docx", "public/report.docx"]
    assert "report.docx" in conflicts[0]["message"]


def test_rag_title_match_handles_document_name_without_extension():
    title = (
        "_\u041f\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u0435_"
        "\u00ab\u041b\u0443\u0447\u0448\u0438\u0435_\u043f\u0440\u0430\u043a\u0442\u0438\u043a\u0438_"
        "\u043d\u0430\u0441\u0442\u0430\u0432\u043d\u0438\u0447\u0435\u0441\u0442\u0432\u0430_"
        "\u0432_\u043e\u0431\u0440\u0430\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445_"
        "\u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u044f\u0445.pdf"
    )
    question = (
        "\u0447\u0442\u043e \u043d\u0430\u0445\u043e\u0434\u0438\u0442\u0441\u044f "
        "\u0432 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0435 "
        "\"_\u041f\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u0435_"
        "\u00ab\u041b\u0443\u0447\u0448\u0438\u0435_\u043f\u0440\u0430\u043a\u0442\u0438\u043a\u0438_"
        "\u043d\u0430\u0441\u0442\u0430\u0432\u043d\u0438\u0447\u0435\u0441\u0442\u0432\u0430_"
        "\u0432_\u043e\u0431\u0440\u0430\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0445_"
        "\u043e\u0440\u0433\u0430\u043d\u0438\u0437\u0430\u0446\u0438\u044f\u0445\""
    )
    doc = Document(page_content="", metadata={"title": title, "s3_key": f"private/{title}"})

    assert RAGSystem._matches_query_by_title(question, doc) is True

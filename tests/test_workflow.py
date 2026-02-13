import io
import os
import tempfile

import app as expense_app


def setup_module(module):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    module.db_path = path
    module.upload_dir = tempfile.mkdtemp()
    expense_app.DB_PATH = path
    expense_app.UPLOAD_DIR = expense_app.Path(module.upload_dir)
    expense_app.UPLOAD_DIR.mkdir(exist_ok=True)
    expense_app.init_db()


def teardown_module(module):
    if os.path.exists(module.db_path):
        os.remove(module.db_path)


def test_submit_batch_and_manager_approve_flow():
    client = expense_app.app.test_client()

    data = {
        "row_count": "1",
        "items-0-date": "2026-01-10",
        "items-0-description": "Hotel",
        "items-0-amount": "123.45",
        "items-0-contract_id": "1",
        "items-0-charge_code_id": "1",
        "items-0-receipt": (io.BytesIO(b"fake-receipt"), "receipt.pdf"),
    }

    response = client.post("/employee/new", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"submitted to manager" in response.data

    manager_response = client.get("/manager/queue")
    assert b"Review" in manager_response.data

    approve = client.post("/manager/batch/1/approve", data={"comment": "Looks good"}, follow_redirects=True)
    assert approve.status_code == 200
    assert b"approved" in approve.data

    supervisor_queue = client.get("/supervisor/queue")
    assert b"Review" in supervisor_queue.data

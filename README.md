# Expense Workflow App

A Flask app implementing a multi-expense submission and two-stage approval workflow:

- Employee can submit multiple expense line items at once.
- Every expense line requires a receipt upload.
- Each line is tied to a contract + valid charge code.
- Manager receives submissions first.
- Manager approval routes to final supervisor.
- Rejections return to employee with comments.
- Notification events are logged for each workflow transition.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open: `http://localhost:5000`

## Demo users (hard-coded)

- Employee: Erin Employee
- Manager: Maya Manager
- Supervisor: Sam Supervisor

## Test

```bash
pytest -q
```

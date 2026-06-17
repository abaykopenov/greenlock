def test_task_default_priority():
    from sample_project.models import Task
    t = Task(id=1, title="Some title")
    assert t.priority == "normal"

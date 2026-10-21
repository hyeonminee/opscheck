"""Actual work state: stable stage/attempt IDs shared by SSE and saved snapshots."""
from datetime import datetime, timezone
from langgraph.config import get_stream_writer

ACTIVE = ('pending', 'running')


def upsert_step(steps, step):
    for index, current in enumerate(steps):
        if current['id'] == step['id']:
            steps[index] = {**current, **step}
            return
    steps.append(dict(step))


def end_steps(steps):
    """A finished or interrupted record never replays a running operation."""
    return [{**s, 'status': 'interrupted' if s['status']=='running' else 'skipped'}
            if s['status'] in ACTIVE else dict(s) for s in steps]


class Progress:
    def __init__(self, state):
        self.steps = [dict(s) for s in state.get('progress', [])]
        try:
            self.writer = get_stream_writer()
        except RuntimeError:
            self.writer = lambda event: None

    def set(self, stage, label, status, attempt=1, **details):
        key = f'{stage}:attempt:{attempt}'
        previous = next((s for s in self.steps if s['id']==key), {})
        now = datetime.now(timezone.utc).isoformat()
        step = {**previous, 'id':key, 'stage_id':stage, 'label':label, 'status':status,
                'attempt':attempt, 'updated_at':now, **details}
        if status == 'running' and 'started_at' not in step:
            step['started_at'] = now
        if status not in ACTIVE:
            step['finished_at'] = now
        upsert_step(self.steps, step)
        self.writer({'type':'Progress', 'step':step})

    def callback(self, stage, label):
        return lambda attempt,status: self.set(stage,label,status,attempt=attempt+1)

    def finish(self):
        for step in list(self.steps):
            if step['status'] in ACTIVE:
                self.set(step['stage_id'],step['label'],
                         'interrupted' if step['status']=='running' else 'skipped',attempt=step['attempt'])

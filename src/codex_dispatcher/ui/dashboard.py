from PySide6.QtWidgets import QTableWidget, QTableWidgetItem


class Dashboard(QTableWidget):
    def __init__(self):
        super().__init__(0, 11)
        self.setHorizontalHeaderLabels(['Worker', 'Repository', 'Assignment', 'Project', 'Thread',
                                       'Monitor', 'Last check', 'Current Issue', 'Queue', 'Thread 状态', 'Last result'])
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)

    def update_state(self, workers, runtime):
        self.setRowCount(len(workers))
        for row, worker in enumerate(workers):
            state = runtime.get(worker.id, {})
            values = [worker.worker_name, worker.repository, worker.assignment_mode + ': ' + worker.assignment_value,
                      worker.target_project, worker.target_thread_id, state.get('status', 'Paused'),
                      state.get('last_check', '—'), state.get('current_issue', '—'), str(state.get('queue', 0)),
                      state.get('thread_state', '—'), state.get('last_result', '—')]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.setItem(row, col, item)
        for col, width in enumerate([130, 140, 180, 260, 260, 130, 200, 100, 65, 160, 280]):
            self.setColumnWidth(col, width)

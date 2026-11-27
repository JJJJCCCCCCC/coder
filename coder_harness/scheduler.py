"""s12 在 s04 上增加五字段 Cron，队列交付与可选持久化。"""
from datetime import datetime, timedelta, timezone
import threading
import uuid
from .runtime import BOOL, STRING, atomic_json, read_json, emit


def parse_cron(expression):
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron 必须包含分钟、小时、日、月、星期五个字段")
    result = []
    for field, (low, high) in zip(fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]):
        values = set()
        for part in field.split(","):
            span, separator, step = part.partition("/")
            step = int(step) if separator else 1
            if step < 1:
                raise ValueError("步长必须大于零")
            if span == "*":
                start, end = low, high
            elif "-" in span:
                start, end = map(int, span.split("-"))
            else:
                start = end = int(span)
            if not low <= start <= end <= high:
                raise ValueError("Cron 字段越界")
            values.update(range(start, end + 1, step))
        result.append(values)
    return result


def matches(expression, moment):
    minute, hour, day, month, weekday = parse_cron(expression)
    values = expression.split()
    dom, dow = moment.day in day, (moment.weekday() + 1) % 7 in weekday
    date_match = (dom or dow) if values[2] != "*" and values[4] != "*" else (dom and dow)
    return moment.minute in minute and moment.hour in hour and moment.month in month and date_match


class Scheduler:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.state / "cron.json"
        self.jobs = read_json(self.path, {})
        self.lock = threading.RLock()

    def save(self):
        atomic_json(self.path, {key: value for key, value in self.jobs.items() if value["durable"]})

    def schedule(self, cron, prompt, recurring=True, durable=False):
        parse_cron(cron)
        with self.lock:
            task_id = uuid.uuid4().hex[:10]
            self.jobs[task_id] = {"id": task_id, "cron": cron, "prompt": prompt, "recurring": recurring,
                                  "durable": durable, "last": "", "pending": False}
            try:
                self.save()
            except Exception:
                del self.jobs[task_id]
                raise
        emit("定时任务 · 注册", f"{task_id} {cron}，时区 Asia/Shanghai")
        return task_id

    def cancel(self, task_id):
        with self.lock:
            removed = self.jobs.pop(task_id)
            try:
                self.save()
            except Exception:
                self.jobs[task_id] = removed
                raise
        return "已取消"

    def poll(self, moment):
        marker = moment.strftime("%Y-%m-%d %H:%M")
        with self.lock:
            for job in self.jobs.values():
                if not job["pending"] and job["last"] != marker and matches(job["cron"], moment):
                    old = job["last"]
                    job.update(last=marker, pending=True)
                    try:
                        self.save()
                    except Exception:
                        job.update(last=old, pending=False)
                        raise
                    emit("定时任务 · 到期入队", job["id"])
                    self.enqueue(job)

    def enqueue(self, job):
        task_id, marker = job["id"], job["last"]
        def valid():
            with self.lock:
                current = self.jobs.get(task_id)
                return current is not None and current["pending"] and current["last"] == marker
        self.runtime.events.put({"text": f"[定时任务 {task_id} {marker}] {job['prompt']}", "valid": valid})

    def acknowledge(self, answer):
        with self.lock:
            # 仅确认已进入当前工作记录的任务，期间新到期任务仍保持 pending。
            history = str(self.runtime.history)
            previous = read_json(self.path, {})
            for key, job in list(self.jobs.items()):
                if job["pending"] and f"[定时任务 {key} {job['last']}]" in history:
                    job["pending"] = False
                    if not job["recurring"]:
                        del self.jobs[key]
            try:
                self.save()
            except Exception:
                # 持久任务从原快照恢复，保证失败确认可以重试交付。
                self.jobs.update(previous)
                raise

    def loop(self):
        for job in list(self.jobs.values()):
            if job["pending"]:
                self.enqueue(job)
        while not self.runtime.stop.wait(1):
            try:
                self.poll(datetime.now(timezone(timedelta(hours=8))))
            except Exception as error:
                emit("定时任务 · 错误", str(error))


def install(runtime):
    scheduler = Scheduler(runtime)
    runtime.services["scheduler"] = scheduler
    runtime.add("schedule_cron", "以北京时间注册五段式定时任务，进程需保持运行", scheduler.schedule,
                {"cron": STRING, "prompt": STRING, "recurring": BOOL, "durable": BOOL}, ["cron", "prompt"])
    runtime.add("list_crons", "查看定时任务", lambda: list(scheduler.jobs.values()))
    runtime.add("cancel_cron", "取消定时任务", scheduler.cancel, {"task_id": STRING})
    runtime.hook("Stop", scheduler.acknowledge)
    runtime.services["start_scheduler"] = lambda: threading.Thread(target=scheduler.loop, daemon=True).start()

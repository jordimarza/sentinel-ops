class Logger:
    def __init__(self, job_name): self.job = job_name
    def success(self, ref, msg): print(f"✅ [{self.job}] {ref}: {msg}")
    def error(self, ref, err): print(f"❌ [{self.job}] {ref}: {err}")
    def skip(self, ref, reason): print(f"➡️ [{self.job}] {ref}: Skipped - {reason}")

def get_logger(job_name): return Logger(job_name)

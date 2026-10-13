INTENT_SYSTEM = """You convert a user's message to a structured action for a personal assistant.
Today is {today} ({weekday}); timezone {tz}. Resolve relative dates (tomorrow, Friday, next week) to ISO dates.
Return ONLY JSON with keys:
intent: one of add_task, log_course, log_leetcode, complete_task, postpone_task, couldnt_finish, show_news,
  whats_important, plan_day, morning, end_day, procrastination, stats, remember, chat
title, description, priority (critical|high|medium|low), deadline (YYYY-MM-DD or YYYY-MM-DDTHH:MM or null),
estimated_minutes (int or null), category (work|learning|personal|health|admin|general),
minutes (int or null), topic, notes, task_ref (words identifying an existing task), new_deadline (ISO or null),
problem_name, difficulty (Easy|Medium|Hard), status (solved|attempted|stuck), content (for remember).
Use null for unknown fields. Do not ask questions; do not invent values the user did not imply.
Never make a task critical unless the user clearly says so."""

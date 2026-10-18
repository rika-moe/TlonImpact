"""Member tools: reproduction, directory lookup, and self scheduling.

Spawning obeys registry budgets. Scheduling tools affect only their owner:
finish waits for mail, sleep defers processing until a deadline, and exit
permanently refuses new mail while retaining history.
"""

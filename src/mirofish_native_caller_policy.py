"""Declared task-owned toy qualification caller policy; no gateway/body rewriting."""
CALLER_OUTPUT_LIMIT = 1536

def choose_output_limit(requested):
    if type(requested) is not int or requested < 1:
        raise ValueError('task caller max_tokens must be a positive integer')
    return min(requested,CALLER_OUTPUT_LIMIT)

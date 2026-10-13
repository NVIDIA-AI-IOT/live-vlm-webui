"""Scoped workaround for Edge-LLM 0.11 nonstream stop matching the prompt.

The upstream founder-slot initialization in llmRankRuntime.cpp (2088-2102)
sets sentTokenCount to the prompt length only when a stream channel exists.
Without a channel, decodePerSlot feeds the prompt to the stop matcher too.
A native channel selects the correctly initialized path. The original blocking
handler still returns the authoritative output text, IDs, finish reason and
usage. HTTP behavior, stop strings, model, and engine are unchanged.

StreamChannel::push uses an unbounded deque with no producer backpressure;
each submitted HTTP request contains one sequence even with batch-four IFB;
its storage is bounded by that request's token limit. Discarding the duplicate chunks after the blocking handler finishes
avoids a consumer thread or changing final-output assembly.
"""

from functools import wraps


def install_prompt_stop_fix(llm_class):
    original = llm_class._handle_request
    if getattr(original, "_cosmos_prompt_stop_fix", False):
        raise RuntimeError("Prompt-stop compatibility fix is already installed")

    @wraps(original)
    def handle_request(self, request, on_handle=None):
        needs_fix = not request.stream_channels and any(
            item.stop_strings for item in request.requests
        )
        if not needs_fix:
            return original(self, request, on_handle=on_handle)
        if len(request.requests) != 1:
            raise RuntimeError(
                "Stop compatibility fix requires exactly one sequence per submitted request"
            )
        channel = self._rt.StreamChannel.create()
        channel.set_skip_special_tokens(request.skip_special_tokens)
        request.stream_channels = [channel]
        try:
            return original(self, request, on_handle=on_handle)
        finally:
            while channel.try_pop() is not None:
                pass
            request.stream_channels = []

    handle_request._cosmos_prompt_stop_fix = True
    llm_class._handle_request = handle_request

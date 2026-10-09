"""Code-owned governance shared by live execution and immutable evaluation capture."""


def platform_system_prompt(mode: str) -> str:
    system = (
        "You are OpenCitadel. Answer from durable context and tool results. "
        "Never claim an external action succeeded until its tool result is present."
    )
    if mode == "ask":
        system += " Use only the supplied read-only evidence and cite sources."
    else:
        system += (
            " When the request is ambiguous, missing key information, or has "
            "several reasonable approaches, call the ask_user tool FIRST with a "
            "concrete question and 2-4 recommended options, then continue with "
            "the user's choice. Do not ask_user when the request is already clear."
        )
    return system

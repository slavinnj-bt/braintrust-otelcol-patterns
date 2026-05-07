#!/usr/bin/env python3
"""
agent.py — Acme Bank AI Customer Support agent with OpenTelemetry instrumentation

Use case: a simulated multi-turn banking support conversation where a customer
provides several types of PII (email, credit card, phone, date of birth,
account number, and dollar amounts). All span attributes and conversation content
containing PII are sent to the OTEL Collector first; the collector's redaction
processor strips PII patterns before any data reaches Braintrust or Grafana.

Architecture:
    This Python process ──OTLP HTTP──► otelcol-contrib (port 4318)
                                            │
                              ┌─────────────┴───────────────┐
                              ▼                             ▼
                    api.braintrust.dev/otel        lgtm:4318 (Grafana)
                    (sees redacted spans only)     (sees redacted spans only)

Tracing hierarchy (visible in Grafana Tempo and Braintrust):
    banking-support-session              ← root span (customer context + session metadata)
    ├── turn-1  (account inquiry)        ← customer email + account ID
    ├── turn-2  (identity verification)  ← account number + date of birth
    ├── turn-3  (dispute filing)         ← credit card number + dollar amount
    └── turn-4  (profile update)         ← phone number + email update

The OpenAI Agents SDK runs inside each turn span. The SDK manages its own
internal tracing (agent-run and LLM-generation spans) and by default exports
them to OpenAI's tracing backend. That path is independent of — and parallel to —
our OTel collector pipeline. To disable the SDK's own tracing and keep everything
in the OTel pipeline only, see the set_trace_processors stub below.

PII redaction layers:
    1. blocked_keys in collector.yaml   — masks entire attribute values by key name
                                          (e.g. customer.account_id, customer.credit_card)
    2. blocked_values in collector.yaml — masks any value matching PII regex patterns
                                          (e.g. emails, phone numbers, card numbers,
                                           dollar amounts that appear inside free-text
                                           attributes like turn.prompt)

References:
    https://www.braintrust.dev/docs/integrations/sdk-integrations/opentelemetry
    https://opentelemetry.io/docs/collector/architecture/
    https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/redactionprocessor/README.md
    https://openai.github.io/openai-agents-python/
    https://openai.github.io/openai-agents-python/tracing/
"""

import os
import sys

from agents import Agent, Runner
from dotenv import load_dotenv

# ── OpenTelemetry imports ─────────────────────────────────────────────────────
# These packages are installed by `braintrust[otel]`, which pins compatible
# versions of opentelemetry-sdk, opentelemetry-api, and the OTLP exporters.
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# Load BRAINTRUST_API_KEY and OPENAI_API_KEY from a .env file.
load_dotenv()

# ── Collector endpoint ────────────────────────────────────────────────────────
# Override via OTEL_EXPORTER_OTLP_ENDPOINT in your shell or .env if the
# collector runs on a different host (e.g., in a remote Docker environment).
COLLECTOR_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

# ── Fake customer record ──────────────────────────────────────────────────────
# This data is entirely fictional and is used only to demonstrate PII redaction.
# In a real application these values would come from your database or session.
# The collector will replace all of these values with ***** before they reach
# Braintrust or Grafana — neither backend ever sees the raw PII.
FAKE_CUSTOMER = {
    "name": "Jane Smith",
    "account_id": "ACC-82734",         # account number — redacted by collector
    "email": "jane.smith@personalmail.com",
    "phone": "(415) 867-5309",
    "credit_card": "4242424242424242", # Stripe test Visa — https://docs.stripe.com/testing
    "dob": "1987-03-14",               # date of birth
}

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are Ava, an AI customer service agent for Acme Bank. "
    "Help customers with account inquiries, transaction disputes, and profile updates. "
    "When customers provide verification details (account number, DOB), acknowledge receipt and proceed. "
    "Never repeat sensitive numbers back in your response. "
    "Keep replies to 2–3 sentences maximum."
)

# ── Conversation turns ────────────────────────────────────────────────────────
# Each turn simulates a realistic customer message that naturally embeds PII.
# These strings are stored as span attributes (turn.prompt) which means they
# flow through the collector pipeline and have PII redacted before export.
TURNS = [
    # Turn 1 — account balance inquiry: contains email address
    (
        f"Hi, this is {FAKE_CUSTOMER['name']}. My email on file is "
        f"{FAKE_CUSTOMER['email']} and I need to check the balance for "
        f"account {FAKE_CUSTOMER['account_id']}."
    ),
    # Turn 2 — identity verification: contains date of birth and account number
    (
        f"To verify my identity: my date of birth is {FAKE_CUSTOMER['dob']} "
        f"and my account number is {FAKE_CUSTOMER['account_id']}. "
        f"Please show my last five transactions."
    ),
    # Turn 3 — dispute: contains full 16-digit credit card number
    (
        f"I see a $299.00 charge from ACME-MERCHANT I don't recognize. "
        f"My full card number is {FAKE_CUSTOMER['credit_card']}. "
        f"Please open a dispute for that transaction."
    ),
    # Turn 4 — profile update: contains phone number and email address
    (
        f"Last thing: please update my callback number to {FAKE_CUSTOMER['phone']} "
        f"and send the dispute confirmation to {FAKE_CUSTOMER['email']}."
    ),
]


def configure_otel() -> trace.Tracer:
    """
    Set up the OpenTelemetry TracerProvider for this Python process.

    Spans flow: this process → OTLP HTTP → otelcol-contrib → (Braintrust + Grafana).
    We do NOT export directly to either backend; all redaction and fan-out
    happens inside the collector (see collector.yaml).

    Note: The OpenAI Agents SDK uses its own separate tracing backend (sends to
    OpenAI's API by default). That tracing path is independent of this OTel
    TracerProvider. See the set_trace_processors stub in run_support_session()
    for how to disable or redirect the SDK's own tracing.
    """
    resource = Resource.create({
        "service.name": "acme-bank-support-agent",
        "service.version": "1.0.0",
        "deployment.environment": "development",
    })

    exporter = OTLPSpanExporter(
        # The OTLP HTTP traces sub-path. The collector's OTLP HTTP receiver
        # listens on port 4318 and expects spans at /v1/traces.
        endpoint=f"{COLLECTOR_ENDPOINT}/v1/traces",
    )

    provider = TracerProvider(resource=resource)
    # BatchSpanProcessor groups spans and exports asynchronously to reduce
    # overhead. Spans are flushed at most every `schedule_delay_millis` or
    # when the batch reaches `max_export_batch_size`.
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Register as the global provider so trace.get_tracer() works anywhere.
    trace.set_tracer_provider(provider)

    # ── STUB: add a console exporter for local debugging ──────────────────────
    # WARNING: ConsoleSpanExporter prints RAW (un-redacted) spans to stdout.
    # Use only in local dev; never in production or shared environments.
    # from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
    # provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # ── STUB: bypass the collector and send directly to Braintrust ───────────
    # Skips all redaction and sampling. Only suitable for non-PII workloads.
    # from braintrust.otel import BraintrustSpanProcessor
    # provider.add_span_processor(BraintrustSpanProcessor())

    return trace.get_tracer("acme-bank-support-example")


def run_support_session(tracer: trace.Tracer) -> None:
    """
    Execute a 4-turn banking support conversation under a single root span.

    PII is intentionally placed in span attributes at different levels of the
    span hierarchy to show that the collector's redaction processor handles all
    of them uniformly — regardless of span depth or attribute key name.

    Layer 1 (blocked_key_patterns):  customer.account_id, customer.credit_card, customer.dob,
                             customer.phone, customer.email — entire value masked.
    Layer 2 (blocked_values): free-text attributes such as turn.prompt that embed
                             PII inline (emails, card numbers, dollar amounts,
                             account numbers) — matched values replaced with *****.
    """
    # ── STUB: disable the OpenAI Agents SDK's default tracing ─────────────────
    # By default the SDK exports its own agent-run and LLM-generation spans to
    # OpenAI's tracing API. Call set_trace_processors([]) to suppress that path
    # so that only your OTel spans (below) are exported — useful when you want
    # a single, collector-controlled pipeline with no data leaving to OpenAI.
    #
    # from agents import set_trace_processors
    # set_trace_processors([])
    #
    # Alternatively, to send the SDK's spans to your own OTLP backend, supply
    # a custom processor:
    # from agents import set_trace_processors
    # from agents.tracing.processors import MyCustomProcessor
    # set_trace_processors([MyCustomProcessor(endpoint=COLLECTOR_ENDPOINT)])

    # Create the OpenAI Agents SDK agent.
    # `instructions` becomes the system prompt; `model` selects the OpenAI model.
    agent = Agent(
        name="Ava",
        instructions=SYSTEM_PROMPT,
        # ── STUB: switch model ────────────────────────────────────────────────
        # Available models: gpt-4o, gpt-4o-mini, gpt-4.1, gpt-4.1-mini, etc.
        # See https://platform.openai.com/docs/models for the full list.
        model="gpt-4o-mini",
    )

    # input_messages carries the full conversation history in OpenAI message
    # format. We start empty and grow it each turn by appending the user
    # message and then replacing it with result.to_input_list(), which returns
    # the complete history including the assistant's reply.
    input_messages: list[dict] = []

    with tracer.start_as_current_span("banking-support-session") as root_span:
        # ── Session-level metadata ─────────────────────────────────────────────
        root_span.set_attribute("session.turns", len(TURNS))
        root_span.set_attribute("agent.framework", "openai-agents")
        root_span.set_attribute("agent.project", "braintrust-otelcol-examples")

        # ── PII attributes — Layer 1 redaction target (blocked_key_patterns) ───
        # These keys match the `blocked_key_patterns` regexes in collector.yaml.
        # Their values are masked to ***** regardless of format.
        # This is the right approach for well-structured PII: you know the key
        # name ahead of time, so declare it explicitly rather than relying on a
        # fragile value regex.
        root_span.set_attribute("customer.name", FAKE_CUSTOMER["name"])
        root_span.set_attribute("customer.email", FAKE_CUSTOMER["email"])
        root_span.set_attribute("customer.phone", FAKE_CUSTOMER["phone"])
        root_span.set_attribute("customer.account_id", FAKE_CUSTOMER["account_id"])
        root_span.set_attribute("customer.credit_card", FAKE_CUSTOMER["credit_card"])
        root_span.set_attribute("customer.dob", FAKE_CUSTOMER["dob"])

        print("=" * 62)
        print("Acme Bank — AI Customer Support Session")
        print(f"Customer:  {FAKE_CUSTOMER['name']}  |  Account: {FAKE_CUSTOMER['account_id']}")
        print(f"Collector: {COLLECTOR_ENDPOINT}")
        print("Traces → Braintrust (project: braintrust-otelcol-examples)")
        print("Traces → Grafana    (http://localhost:3000)")
        print("NOTE: PII is redacted at the collector — backends see ***** only")
        print("=" * 62)

        for i, prompt in enumerate(TURNS, start=1):
            turn_name = f"turn-{i}"
            print(f"\n── Turn {i} ──────────────────────────────────────────")
            print(f"Customer: {prompt}")

            with tracer.start_as_current_span(turn_name) as turn_span:
                turn_span.set_attribute("turn.number", i)

                # turn.prompt stores the raw customer message including any PII
                # the customer typed. The collector's blocked_values regex
                # patterns will find and replace emails, SSNs, phone numbers,
                # and card numbers embedded in this free-text string.
                turn_span.set_attribute("turn.prompt", prompt)

                # Append the new user message to the running history.
                input_messages.append({"role": "user", "content": prompt})

                # Runner.run_sync() executes the agent synchronously and returns
                # a RunResult. The SDK handles the OpenAI API call internally,
                # generating its own agent-run and LLM-generation spans (sent to
                # OpenAI's tracing API by default — see the stub above to change
                # that behavior).
                result = Runner.run_sync(agent, input_messages)

                reply = result.final_output

                # Replace input_messages with the full history returned by the
                # SDK. This includes the assistant reply and is ready to be
                # passed directly to the next Runner.run_sync() call.
                input_messages = result.to_input_list()

                turn_span.set_attribute("turn.reply_preview", reply[:120])

                print(f"Ava:      {reply}")

        root_span.set_attribute("session.completed", True)
        print("\n" + "=" * 62)
        print("Session complete. Flushing spans to collector...")


def main() -> None:
    missing = [k for k in ("BRAINTRUST_API_KEY", "OPENAI_API_KEY") if not os.getenv(k)]
    if missing:
        print(f"ERROR: Missing environment variable(s): {', '.join(missing)}")
        print("Copy ../.env.example to .env and fill in your API keys.")
        sys.exit(1)

    tracer = configure_otel()

    try:
        run_support_session(tracer)
    finally:
        # Flush buffered spans before exit. Without this, BatchSpanProcessor
        # may drop spans queued but not yet sent.
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)

    print("Done.")
    print("  Grafana:    http://localhost:3000 → Explore → Tempo")
    print("  Braintrust: https://www.braintrust.dev → project: braintrust-otelcol-examples")
    print("  Collector:  http://localhost:8888/metrics → otelcol_exporter_sent_spans")


if __name__ == "__main__":
    main()

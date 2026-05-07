# CLAUDE.md

## Goal

You are creating an complete, deployable example of an LLM agent instrumented with OpenTelemetry, sending traces to an OTEL collector, and routed to both Grafana and Braintrust. The example is intended to show a user how to use Braintrust with existing OpenTelemetry patterns and infrastructure. The example should include routing to two backends, Grafana and Braintrust, PII redaction in the trace, and sampling prior to ingest.

Create a step by step plan to implement this project.

Follow the instructions in each section below and clearly digest them before beginning implementation.
 
## Bill of materials
You will use the following components in the solution:
- LGTM stack and OTEL collector: `https://github.com/grafana/docker-otel-lgtm/`
- Agent framework (OpenAI Agents SDK): `https://openai.github.io/openai-agents-python/`
- OpenTelemetry SDK: `https://www.braintrust.dev/docs/integrations/sdk-integrations/opentelemetry`
- Braintrust API key: provided as `BRAINTRUST_API_KEY` in `.env` file
- OpenAI API key: provided as `OPENAI_API_KEY` in `.env` file


## Capabilities / Documentation
You will use the following guidelines/docs for implementing features:
- Redaction - OTEL redaction processor: `https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/redactionprocessor/README.md`
- Fan-out collector architecture: `https://opentelemetry.io/docs/collector/architecture/`
- Setting x-bt-parent header to route traces to the collector: `https://www.braintrust.dev/docs/integrations/sdk-integrations/opentelemetry#otlp-configuration`
 
## Implementation details
- Be sure to use the `braintrust[otel]` sdk for Python: https://www.braintrust.dev/docs/integrations/sdk-integrations/opentelemetry#python-sdk-configuration
- project_name: "braintrust-otelcol-examples"
- Traces should contain multi-turn spans, use "Manual tracing" as described here only if necessary: https://www.braintrust.dev/docs/integrations/sdk-integrations/opentelemetry#manual-tracing  Rely on as little code as possible: 
- Use the docs for the OpenAI Agents SDK to determine how the SDK works: https://openai.github.io/openai-agents-python/running_agents/ and https://openai.github.io/openai-agents-python/tracing/
- Use detailed comments for your code to explain what is happening, this includes both the agent tracing code and the `collector.yaml` and other OTEL config files
- Make the example easily extensible; for example, add stubs or commented out code for adding: new models, new observability backends, new processors/receivers/connectors/exporters.
- Clearly explain each OTEL collector component in the README and code comments; more information found here.
- Use only official OpenTelemetry documentation in your research, such as `opentelemetry.io` and its subdomains, Grafana documentation (`grafana.com/docs` and `https://grafana.com/docs/opentelemetry/docker-lgtm/`), Braintrust documentation (`https://www.braintrust.dev/docs`), and the [`otel-collector-contrib`](https://github.com/open-telemetry/opentelemetry-collector-contrib/) on GitHub.
- Create clear steps for building and running the examples.
- Show your references in the README. Do not make unverified claims. Ensure that every claim, recommendation, or best practice you provide can be backed up by the documentation above, preferably with citations.

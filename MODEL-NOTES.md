
Notes On Models Tested
======================

The following ollama models were tested in this evaluation:

- batiai/qwen3.6-35b:q[34]
- qwen3.5:9b
- qwen3-coder:30b
- glm-4.7-flash:q4_K_M
- qwen2.5:14b

Notes follow:

## batiai/qwen3.6-35b:q3

This model did not respond well to working in an agentic manner.

When running this command:

```bash
echo "Who would win in godzilla vs mothra? Create your response in markdown. Write the response to this file: /tmp/batiai-q3.md. Exit when finished." | aicli --model batiai/qwen3.6-35b:q3 --include-directories /tmp  --auto-approve --allow-exec
```
I let it run for several mintues, then killed it due to lack of response.

## batiai/qwen3.6-35b:q4

This model did respond and create good response, and it did exi when finished.

Running this command:

```bash
echo "Who would win in godzilla vs mothra? Create your response in markdown. Write the response to this file: /tmp/batiai-q4.md. Exit when finished." | aicli --model batiai/qwen3.6-35b:q4 --include-directories /tmp  --auto-approve --allow-exec
```

## qwen3.5:9b

This model create a good response, and exited when finished.

## qwen3-coder:30b

This model create a good response, and exited when finished.

## glm-4.7-flash:q4_K_M

This model create an excellent response, and exited when finished.

## qwen2.5:14b

This model create a good response, and exited when finished.



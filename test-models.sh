#!/usr/bin/env bash

prompt="Who would win in godzilla vs mothra? Create your response in markdown. Write the response to this file: <<RESPONSE_FILE>>. Exit when finished."


logDir='logs';
mkdir -p $logDir
logFile=$logDir/runlog-$(date +%Y-%m-%d_%H-%M-%S).log

> $logFile
exec 1> >(tee -ia $logFile)
exec 2> >(tee -ia $logFile >&2)

# set the time format to something more easily parsed
export TIMEFORMAT='%R:%U:%S'

for model in $(aicli --list-models); do
	echo "Testing model: $model"
	reponseFilename="/tmp/response_${model//\//_}.md"
	echo "Response will be written to: $reponseFilename"
	rm -f $reponseFilename # remove old response file if it exists
	tmpPrompt="${prompt//<<RESPONSE_FILE>>/$reponseFilename}"
	echo "Prompt: $tmpPrompt"
	echo "start time: $(date +%Y-%m-%d\ %H:%M:%S)"
	traceFile=$logDir/trace-$(date +%Y-%m-%d_%H-%M-%S).log && > $traceFile
	time echo $tmpPrompt | aicli --trace $traceFile --stream-timeout 120 --model $model  --include-directories /tmp  --auto-approve --allow-exec
	echo "  end time: $(date +%Y-%m-%d\ %H:%M:%S)"
	echo "=============================="
done


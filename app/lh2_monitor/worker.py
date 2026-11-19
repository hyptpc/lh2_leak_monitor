"""One live operation. Only launched explicitly by ProcessExecutor."""

import json
import sys


def main():
    if sys.argv[1:] != ['--live-worker']:
        print('This worker requires --live-worker; no operation performed.', file=sys.stderr)
        return 2
    from .hardware import perform

    try:
        request = json.load(sys.stdin)
        result = perform(request['kind'], request['parameters'], request['timeout'])
        if isinstance(result, str):
            result = {'detail': result}
        print('LH2_RESULT_JSON:' + json.dumps(result), flush=True)
        return 0
    except Exception as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())

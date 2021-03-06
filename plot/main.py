import os
import csv
import time
import json
import subprocess
from datetime import datetime
from io import StringIO
from functools import partial

REPO_URL = 'https://github.com/pytorch/pytorch'
REPO_DIR = 'repo'
OUTPUT_PATH = 'results.json'
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BENCHES = 100
BENCH_EVERY = 10 # th commit

run = partial(subprocess.check_call, cwd=REPO_DIR)
run_with_output = partial(subprocess.check_output, cwd=REPO_DIR)
run_toplevel = subprocess.check_call
silent = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def fetch_repo():
    if os.path.exists(REPO_DIR):
        print('> Pulling new changes...')
        run(['git', 'checkout', 'master'], **silent)
        run(['git', 'pull'], **silent)
        return
    print('> Cloning repository...')
    run_toplevel(['git', 'clone', '--recursive', REPO_URL, REPO_DIR], **silent)

def get_history():
    # git log --format='%H %an %ae %at' -n <num commits>
    fields = [
        ('hash', '%H', str),
        ('author_name', '%an', str),
        ('author_email', '%ae', str),
        ('commit_time', '%at', int),
    ]
    fmt = "{}".format(','.join('"{}"'.format(field) for name, field, tp in fields))
    run(['git', 'checkout', 'master'], **silent)
    output = run_with_output(['git', 'log', '--format=' + fmt]).decode('utf8')
    f = StringIO(output)
    reader = csv.DictReader(StringIO(output), fieldnames=[name for name, field, tp in fields])
    # Reverse to ensure that older commits come first
    all_commits = list(reversed([{name: tp(row[name]) for name, field, tp in fields} for row in reader]))
    return all_commits[::BENCH_EVERY][-MAX_BENCHES:]



def build(commit_hash):
    os.environ['NO_TEST'] = '1'
    os.environ['BUILD_CAFFE2_OPS'] = '0'
    start = time.time()
    run(['git', 'checkout', commit_hash], **silent)
    run(['git', 'clean', '-xfd'], **silent)
    run(['git', 'submodule', 'update', '--init', '--recursive'], **silent)
    run(['python', 'setup.py', 'install'])
    end = time.time()
    diff = int(end - start)
    print('    (Build took {} min {} s)'.format(diff // 60, diff % 60))


def load_results():
    if not os.path.exists(OUTPUT_PATH):
        return []
    with open(OUTPUT_PATH, 'r') as f:
        return json.load(f)


def align_commits(commits, results):
    if not results:
        return commits

    def find_offset():
        # TODO: it's enough to check boundaries (mutually)
        # This could easily be improved to run in O(n log(n)), but who cares.
        for i, c in enumerate(commits):
            for j, result in enumerate(results):
                if c['hash'] == result['hash']:
                    return i, j
        return None

    offset = find_offset()
    if offset is None:
        raise RuntimeError("Existing results don't share even a single commit with the tested range!")
    commit_idx, result_idx = offset

    new_early_commits, new_late_commits = [], []

    leading_commits = commit_idx
    leading_results = result_idx
    if leading_commits > leading_results:
        new_early_commits = commits[:(leading_commits - leading_results)]

    trailing_commits = len(commits) - commit_idx
    trailing_results = len(results) - result_idx
    if trailing_commits > trailing_results:
        new_late_commits = commits[-(trailing_commits - trailing_results):]

    return new_early_commits + results + new_late_commits


def merge_into(original, new):
    for key in new:
        if key in original:
            assert isinstance(original[key], dict)
            merge_into(original[key], new[key])
        else:
            original[key] = new[key]

def print_plan(to_bench):
    if not to_bench:
        print('> Nothing to do!')
        return
    print('> Building {} commits:'.format(len(to_bench)))
    print('\n'.join('    - {} from {}'.format(result['hash'], datetime.fromtimestamp(result['commit_time'])) for result in to_bench))


BENCHMARKS = [
    dict(args=['python', '-m', 'fastrnns.bench', '--print-json'], cwd=os.path.join(HERE, '..', 'rnns')),
]

fetch_repo()
new_commits = get_history()
existing_results = load_results()
all_commits = align_commits(new_commits, existing_results)
to_bench = [commit for commit in all_commits if 'times' not in commit][-MAX_BENCHES:]
print_plan(to_bench)
try:
    for i, commit in enumerate(to_bench):
        if 'times' in commit:
            continue
        try:
            print('> Building {} ({}/{})...'.format(commit['hash'], i + 1, len(to_bench)))
            build(commit['hash'])
            times = {}
            for args in BENCHMARKS:
                output = run_with_output(**args).decode('utf8')
                merge_into(times, json.loads(output))
            commit['times'] = times
        except Exception as e:
            print('Interrupted by an exception! Saving partial results...')
            print(e)
except KeyboardInterrupt:
    print('Received an interrupt. Saving partial results...')

with open(OUTPUT_PATH, 'w') as f:
    json.dump(all_commits, f)

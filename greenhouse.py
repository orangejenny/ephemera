import argparse
import json
import math
import os.path
import pprint
import re
import requests
import sys
from requests.auth import HTTPBasicAuth

parser = argparse.ArgumentParser(description='Analyze applicant data')
parser.add_argument('--token', help='API token')
parser.add_argument('--refresh', action='store_true', help='Overwrite data files')
arguments = parser.parse_args(sys.argv[1:])

BASE_URL = "https://harvest.greenhouse.io/v1/"
TOKEN = arguments.token
REFRESH_DATA = arguments.refresh
DATA_DIR = "data"
printer = pprint.PrettyPrinter(indent=4)    # pp.pprint(stuff)

def list_things(type):
    filename = "{}/{}.json".format(DATA_DIR, type)
    if os.path.isfile(filename) and not REFRESH_DATA:
        print "reading from {}".format(filename)
        with open(filename) as data:
            all_things = json.load(data)
    else:
        all_things = []
        things = []
        page = 1
        while page == 1 or len(things):
            response = requests.get("{}/{}/?per_page=500&page={}".format(BASE_URL, type, page), auth=HTTPBasicAuth(TOKEN, ''))
            things = json.loads(response.text)
            print "fetching page {} of {}".format(page, type)
            if response.status_code != 200:
                raise Exception("{}: {}".format(response.status_code, response.text))
            all_things = all_things + things
            page = page + 1
        with open(filename, 'w') as outfile:
            json.dump(all_things, outfile)

    print "found {} {}".format(len(all_things), type)
    return all_things

def binary_result(scorecard):
    if re.search(r'yes', scorecard['overall_recommendation']):  # yes / strong_yes
        return '1'
    elif re.search(r'no', scorecard['overall_recommendation']): # no / definitely_not
        return '0'
    return ''

def calibrate_interviewers(scorecards, scorecard_filter=None):
    interviewers = {}
    for s in dev_scorecards:
        if not scorecard_filter or scorecard_filter(s):
            name = s['submitted_by']['name']
            if name not in interviewers:
                interviewers[name] = ''
            interviewers[name] = interviewers[name] + binary_result(s)
    return interviewers

departments = list_things('departments')
tech_department_id = next(d['id'] for d in departments if d['name'].lower() == 'tech')

jobs = list_things('jobs')
tech_job_ids = set([j['id'] for j in jobs if tech_department_id in [d['id'] for d in j['departments']]])

applications = list_things('applications')  # alternatively, could list the applications for each tech job id
tech_application_ids = set([a['id'] for a in applications if set([j['id'] for j in a['jobs']]) & tech_job_ids])

#candidates = list_things('candidates')
#tech_candidate_ids = [c['id'] for c in candidates if set([a['id'] for a in c['applications']]) & tech_application_ids]

scorecards = list_things('scorecards')
tech_scorecards = [s for s in scorecards if s['application_id'] in tech_application_ids]

tech_interview_types = set([s['interview'] for s in tech_scorecards])
dev_scorecards = [s for s in tech_scorecards if re.search(r'^Dev ', s['interview']) and not re.search(r'Application Review', s['interview'])]
dev_interview_types = set([s['interview'] for s in dev_scorecards])

dev_scorecards_by_interview_type = {}
for t in dev_interview_types:
    dev_scorecards_by_interview_type[t] = [s for s in dev_scorecards if s['interview'] == t]
    print "{}: {} scorecards".format(t, len(dev_scorecards_by_interview_type[t]))

phone_screen_scorecards = []
second_round_scorecards = []
final_round_scorecards = []
for type, scorecards in dev_scorecards_by_interview_type.iteritems():
    if re.search(r'round 1', type.lower()):
        phone_screen_scorecards = phone_screen_scorecards + scorecards
    elif re.search(r'round 2', type.lower()) or re.search(r'second round', type.lower()):
        second_round_scorecards = second_round_scorecards + scorecards
    elif type == 'Dev Peer Panel Review':
        final_round_scorecards = final_round_scorecards + scorecards

# Pass rates on each round
phone_screen_candidates = set([s['candidate_id'] for s in phone_screen_scorecards])
second_round_candidates = set([s['candidate_id'] for s in second_round_scorecards])
final_round_candidates = set([s['candidate_id'] for s in final_round_scorecards])
final_round_applications = set([s['application_id'] for s in final_round_scorecards])
applications_by_id = {a['id']: a for a in applications}
final_round_applications_not_rejected = [a for a in final_round_applications if applications_by_id[a]['status'] != 'rejected']

print ""
print "FUNNEL"
print "{} phone screens".format(len(phone_screen_candidates))
print "{} second rounds ({}% of phone screens)".format(len(second_round_candidates), math.floor(len(second_round_candidates) * 100 / len(phone_screen_candidates) + 0.5))
print "{} final rounds ({}% of second rounds)".format(len(final_round_candidates), math.floor(len(final_round_candidates) * 100 / len(second_round_candidates) + 0.5))
print "{}% of final rounds passed (or are still active)".format(len(final_round_applications_not_rejected) * 100 / len(final_round_candidates))
print ""

# Phone screen question frequency
print ""
print "PHONE SCREENS"
phone_screen_questions = {}     # id => { questions => set(), answers => { 'answer' => count } }
for s in phone_screen_scorecards:
    for q in s['questions']:
        if q['id']:     # the notes questions don't have ids
            if q['answer'] in ['Average', 'Strong', 'Weak']:
                name = q['question'].lower()
                name = re.sub(r'^q[0-9]*\s*:?', '', name)
                if name not in phone_screen_questions:
                    phone_screen_questions[name] = {}
                if q['answer'] not in phone_screen_questions[name]:
                    phone_screen_questions[name][q['answer']] = 0
                phone_screen_questions[name][q['answer']] = phone_screen_questions[name][q['answer']] + 1
printer.pprint(phone_screen_questions)

# Frequency with which 2nd round tech interviews disagree
technical_second_round_results = {}
for s in second_round_scorecards:
    if not re.search(r'non.technical', s['interview'].lower()):
        cid = s['candidate_id']
        result = ''
        if cid in technical_second_round_results:
            result = technical_second_round_results[cid]
        technical_second_round_results[cid] = result + binary_result(s)
passed_both = set([cid for cid, result in technical_second_round_results.iteritems() if result == '11'])
failed_both = set([cid for cid, result in technical_second_round_results.iteritems() if result == '00'])
disagreements = set([cid for cid, result in technical_second_round_results.iteritems() if result in ['01', '10']])
total = len([cid for cid, result in technical_second_round_results.iteritems() if len(result) == 2])
final_round_candidates_not_rejected = set([applications_by_id[a]['candidate_id'] for a in final_round_applications_not_rejected])

print ""
print "SECOND ROUND TECHNICAL ({} total interviews)".format(total)
print "{} passed both ({}%)".format(len(passed_both), math.floor(len(passed_both) * 100 / total + 0.5))
print "{} failed both ({}%)".format(len(failed_both), math.floor(len(failed_both) * 100 / total + 0.5))
print "{} disagreed ({}%)".format(len(disagreements), math.floor(len(disagreements) * 100 / total + 0.5))
print "Of the disagreements, {}% went on to final round".format(math.floor(len(disagreements & final_round_candidates) * 100 / len(disagreements) + 0.5))
print "Of the disagreements, {}% were ultimately rejected".format(math.floor((len(disagreements) - len(disagreements & final_round_candidates_not_rejected)) * 100 / len(disagreements) + 0.5))
print ""

print "FINAL ROUNDS"
final_round_results = {}
outcomes = {
    'definitely_not': 'a',
    'no': 'b',
    'yes': 'c',
    'strong_yes': 'd',
}
for s in final_round_scorecards:
    if s['overall_recommendation'] in outcomes:
        if s['application_id'] not in final_round_results:
            final_round_results[s['application_id']] = []
        final_round_results[s['application_id']].append(outcomes[s['overall_recommendation']])
final_round_results_sorted = [{
    'status': applications_by_id[application_id]['status'],
    'results': "".join(sorted(results)),
} for application_id, results in final_round_results.iteritems()]
final_round_results_sorted.sort(key=lambda x: x['results'])
for final_round in final_round_results_sorted:
    print "{} => {}".format(final_round['results'].replace('a', '.').replace('b', '0').replace('c', '1').replace('d', '*'), final_round['status'])
print ""


# Pass rates for specific users

print "INTERVIEWERS"
def _scorecard_is_final(s):
    return s['interview'] == 'Dev Peer Panel Review'
def _scorecard_is_not_final(s):
    return s['interview'] != 'Dev Peer Panel Review'
all_interviewers = calibrate_interviewers(dev_scorecards)
final_round_interviewers = calibrate_interviewers(dev_scorecards, scorecard_filter=_scorecard_is_final)
non_final_round_interviewers = calibrate_interviewers(dev_scorecards, scorecard_filter=_scorecard_is_not_final)
for name, results in all_interviewers.iteritems():
    non_final_round_stats = "no non-final rounds"
    if name in non_final_round_interviewers:
        non_final_round_stats = "{}% over {} non-final rounds".format(
            len(non_final_round_interviewers[name].replace("0", "")) * 100 / len(non_final_round_interviewers[name]), len(non_final_round_interviewers[name]),
        )
    final_round_stats = "no final rounds"
    if name in final_round_interviewers:
        final_round_stats = "{}% over {} final rounds".format(
            len(final_round_interviewers[name].replace("0", "")) * 100 / len(final_round_interviewers[name]), len(final_round_interviewers[name])
        )
    print "{} has a {}% pass rate over {} interviews ({}, {})".format(name, len(results.replace("0", "")) * 100 / len(results), len(results), non_final_round_stats, final_round_stats)
print ""


# TODO: phone screen: frequency with which questions are asked
# TODO: phone screen: how well do question responses correlate with eventual success?

# TODO: How often does an earlier interviewer change their mind by the final round? (Should they bother coming?)

# TODO: Filter interview types better, limit to exact the expected types
# TODO: deal with applications, not candidates
# TODO: move each section into a function to more easily comment out
'''
- has an id
- has a series of stages, each with 1 or more scorecards
- has an overall status
'''

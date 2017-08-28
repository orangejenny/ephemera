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
parser.add_argument('--verbose', action='store_true', help='Print verbose debugging')
arguments = parser.parse_args(sys.argv[1:])
printer = pprint.PrettyPrinter(indent=4)    # pp.pprint(stuff)

BASE_URL = "https://harvest.greenhouse.io/v1/"
TOKEN = arguments.token
DATA_DIR = "data"

DEV_INTERVIEW_STAGES = {
    'Dev Round 1 - Phone Screen': 0,
    'Dev Round 1 - Technical Interview': 0,
    'Dev Round 2 - Non-Technical Interview': 1,
    'Dev Round 2 - Technical Interview 1': 1,
    'Dev Round 2 - Technical Interview 2': 1,
    'Dev Second Round Technical Interview': 1,
    'Dev Peer Panel Review': 2,
}
DEV_INTERVIEW_TYPES = DEV_INTERVIEW_STAGES.keys()

def list_things(type):
    filename = "{}/{}.json".format(DATA_DIR, type)
    if os.path.isfile(filename) and not arguments.refresh:
        if arguments.verbose:
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
            if arguments.verbose:
                print "fetching page {} of {}".format(page, type)
            if response.status_code != 200:
                raise Exception("{}: {}".format(response.status_code, response.text))
            all_things = all_things + things
            page = page + 1
        with open(filename, 'w') as outfile:
            json.dump(all_things, outfile)

    if arguments.verbose:
        print "found {} {}".format(len(all_things), type)
    return all_things

def binary_result(scorecard):
    if re.search(r'yes', scorecard['overall_recommendation']):  # yes / strong_yes
        return '1'
    elif re.search(r'no', scorecard['overall_recommendation']): # no / definitely_not
        return '0'
    return ''

def percent_of(part, whole):
    return int(part * 100 / whole + 0.5)




# Fetch data, filter down to dev scorecards only
departments = list_things('departments')
tech_department_id = next(d['id'] for d in departments if d['name'].lower() == 'tech')
jobs = list_things('jobs')
tech_job_ids = set([j['id'] for j in jobs if tech_department_id in [d['id'] for d in j['departments']]])
candidates = list_things('candidates')
applications = list_things('applications')
tech_application_ids = set([a['id'] for a in applications if set([j['id'] for j in a['jobs']]) & tech_job_ids])
scorecards = list_things('scorecards')
tech_scorecards = [s for s in scorecards if s['application_id'] in tech_application_ids]
dev_scorecards = [s for s in tech_scorecards if s['interview'] in DEV_INTERVIEW_TYPES]

candidates_by_id = {c['id']: c for c in candidates}
applications_by_id = {a['id']: a for a in applications}
DEV_APPLICATIONS = {}
for s in dev_scorecards:
    if s['application_id'] not in DEV_APPLICATIONS:
        application = applications_by_id[s['application_id']]
        candidate = candidates_by_id[application['candidate_id']]
        DEV_APPLICATIONS[s['application_id']] = {
            'id': s['application_id'],
            'name': "{} {}".format(candidate['first_name'], candidate['last_name']),
            'stages': [
                [],     # phone screen scorecard
                [],     # second round scorecards
                [],     # final round scorecards
            ],
            'status': application['status'],    # note that 'rejected' might mean candidate declined offer, need rejection_direction too
            'rejection_direction': application['rejection_reason']['type']['name'] if application['rejection_reason'] else '',
        }
    DEV_APPLICATIONS[s['application_id']]['stages'][DEV_INTERVIEW_STAGES[s['interview']]].append(s)

FINAL_ROUND_DECISIONS = {
    'rejected': [], # list of application ids
    'offered': [],
    'active': [],
}
for id, a in DEV_APPLICATIONS.iteritems():
    if len(a['stages'][2]):
        if a['status'] == 'active':
            FINAL_ROUND_DECISIONS['active'].append(id)
        if a['status'] == 'hired':
            FINAL_ROUND_DECISIONS['offered'].append(id)
        elif a['status'] == 'rejected' and a['rejection_direction'] == 'They rejected us':
            FINAL_ROUND_DECISIONS['offered'].append(id)
        elif a['status'] == 'rejected' and a['rejection_direction'] == 'We rejected them':
            FINAL_ROUND_DECISIONS['rejected'].append(id)


# Funnel of stages
totals = [0, 0, 0]
for i in range(3):
    totals[i] = len([a for a in DEV_APPLICATIONS.values() if len(a['stages'][i])])

print "\nFUNNEL"
print "{} phone screens".format(totals[0])
print "{} second rounds ({}% of phone screens)".format(totals[1], percent_of(totals[1], totals[0]))
print "{} final rounds ({}% of second rounds)".format(totals[2], percent_of(totals[2], totals[1]))
print "{} final rounds offered ({}% of final rounds)".format(len(FINAL_ROUND_DECISIONS['offered']), percent_of(len(FINAL_ROUND_DECISIONS['offered']), totals[2]))
print "{} final rounds still active ({}% of final rounds)".format(len(FINAL_ROUND_DECISIONS['active']), percent_of(len(FINAL_ROUND_DECISIONS['active']), totals[2]))

# Frequency with which 2nd round tech interviews disagree
technical_results = {}
nontechnical_results = {}
for id, a in DEV_APPLICATIONS.iteritems():
    for s in a['stages'][1]:
        if re.search(r'non.technical', s['interview'].lower()):
            nontechnical_results[id] = binary_result(s)
        else:
            if id not in technical_results:
                technical_results[id] = ''
            technical_results[id] = technical_results[id] + binary_result(s)


passed_both = len([id for id, result in technical_results.iteritems() if result == '11'])
failed_both = len([id for id, result in technical_results.iteritems() if result == '00'])
disagreements = [id for id, result in technical_results.iteritems() if result in ['01', '10']]
total = len([id for id, result in technical_results.iteritems() if len(result) == 2])
final_rounds = [id for id, a in DEV_APPLICATIONS.iteritems() if a['stages'][2]]

print "\nSECOND ROUND TECHNICAL ({} total interviews)".format(total)
print "{} passed both ({}%)".format(passed_both, percent_of(passed_both, total))
print "{} failed both ({}%)".format(failed_both, percent_of(failed_both, total))
print "{} disagreed ({}%)".format(len(disagreements), percent_of(len(disagreements), total))
print "Of the disagreements, {} ({}%) went on to final round".format(len(set(disagreements) & set(final_rounds)), percent_of(len(set(disagreements) & set(final_rounds)), len(disagreements)))
print "Of the disagreements, {} ({}%) ultimately succeeded".format(len(set(disagreements) & set(FINAL_ROUND_DECISIONS['offered'])), percent_of(len(set(disagreements) & set(FINAL_ROUND_DECISIONS['offered'])), len(disagreements)))

print "\nSECOND ROUND NON-TECHNICAL"
print "{}% passed (of {} total non-technical interviews)".format(percent_of(len([r for r in nontechnical_results.values() if r == '1']), len(nontechnical_results)), len(nontechnical_results))


print "\nFINAL ROUNDS"

final_round_results = []
outcomes = ['definitely_not', 'no', 'yes', 'strong_yes']
for a in DEV_APPLICATIONS.values():
    if a['stages'][2]:
        final_round_results.append({
            'application': a,
            'results': "".join(sorted(['1' if re.search(r'yes', s['overall_recommendation']) else '0' for s in a['stages'][2] if s['overall_recommendation'] in outcomes])),
        })
final_round_results.sort(key=lambda x: x['results'])
for final_round in final_round_results:
    status = final_round['application']['status']
    if status == 'rejected' and final_round['application']['id'] in FINAL_ROUND_DECISIONS['offered']:
        status = 'offered, they declined'
    print "{} \t{}=> {}".format(final_round['results'], ("\t" if len(final_round['results']) < 7 else ""), status)



# Pass rates for specific users
interviewers = {}
for a in DEV_APPLICATIONS.values():
    for i, stage in enumerate(a['stages']):
        for scorecard in stage:
            name = scorecard['submitted_by']['name']
            if name not in interviewers:
                interviewers[name] = [[], [], []]
            interviewers[name][i].append(scorecard)

print "\nINTERVIEWERS"
stage_names = ["phone screens", "second rounds", "final rounds"]
interviewer_stats = []
for name, results in interviewers.iteritems():
    stage_stats = []
    for i, stage in enumerate(stage_names):
        stage_stats.append("no " + stage)
        if results[i]:
            passes = 0
            total = 0
            alignment = 0
            for scorecard in results[i]:
                dimagi_passed =  scorecard['application_id'] in FINAL_ROUND_DECISIONS['offered']
                interviewer_passed = bool(re.search(r'yes', scorecard['overall_recommendation']))
                if interviewer_passed:
                    passes = passes + 1
                if interviewer_passed == dimagi_passed:
                    alignment = alignment + 1
                total = total + 1
            stage_stats[i] = "{}% of {} {}".format(percent_of(passes, total), total, stage)

    passes = len([scorecard for stages in results for scorecard in stages if re.search(r'yes', scorecard['overall_recommendation'])])
    total = len([scorecard for stages in results for scorecard in stages])
    interviewer_stats.append({
        'stat': "{} has a {}% pass rate over {} interviews, {}% aligned ({}, {}, {})".format(
            name,
            percent_of(passes, total),
            total,
            percent_of(alignment, total),
            stage_stats[0],
            stage_stats[1],
            stage_stats[2]
        ),
        'overall': percent_of(passes, total),
    })
interviewer_stats.sort(key=lambda x: x['overall'])
for i in interviewer_stats:
    print i['stat']

# TODO: phone screen: frequency with which questions are asked
# TODO: phone screen: how well do question responses correlate with later success?

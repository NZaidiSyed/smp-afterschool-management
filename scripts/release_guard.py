"""Validate release documentation and record authorized UAT/deployment events.

This tool validates evidence records; it does NOT authenticate an approver or
replace protected branches/provider deployment approvals. Never fabricate events.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REGISTER = 'docs/releases/register.json'
MASTER = 'APP_MASTER.md'
START, END = '<!-- RELEASE_HISTORY:START -->', '<!-- RELEASE_HISTORY:END -->'
STATES = {'planned','testing','awaiting_uat','approved','blocked','deployment_started',
          'deployed','deployment_failed','rolled_back','legacy_unverified'}
TRANSITIONS = {
    'planned': {'testing','blocked'},
    'testing': {'testing','awaiting_uat','blocked'},
    'blocked': {'blocked','testing','awaiting_uat'},
    'awaiting_uat': {'approved','testing','blocked'},
    'approved': {'deployment_started','testing','blocked'},
    'deployment_started': {'deployed','deployment_failed'},
    'deployment_failed': {'rolled_back','testing','blocked'},
    'deployed': {'rolled_back'},
    'rolled_back': {'testing','blocked'},
    'legacy_unverified': set(),
}
SHA = re.compile(r'[0-9a-f]{40}\Z')
REQUIRED_GATES = {'automated_tests','hosted_uat','target_identity','data_protection','security_review','release_controls'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value,str) and bool(value.strip()) and value.strip().lower() not in {'todo','tbd','unknown','pending'}


def timestamp(value):
    require(isinstance(value,str),'Event timestamp is required')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds()==0,'Record timestamps must be UTC')
    return parsed


def passed(release):
    require(bool(release.get('checks')),'Required release checks are missing')
    require(REQUIRED_GATES <= set(release['checks']),'Mandatory production gates cannot be removed')
    for name,check in release['checks'].items():
        require(check.get('status')=='passed' and nonempty(check.get('evidence')),
                f'Required check has not passed with evidence: {name}')
        require(check.get('candidate_sha')==release['candidate_sha'],f'Check evidence belongs to a different candidate: {name}')


def validate_approval(release, approval):
    require(isinstance(approval,dict),'Explicit user UAT approval is missing')
    for field in ('approver','reference'):
        require(nonempty(approval.get(field)),f'Approval {field} is required')
    timestamp(approval.get('approved_at'))
    require(approval.get('decision')=='approved_for_production','Explicit production decision is required')
    require(approval.get('release_id')==release['id'],'Approval belongs to another release')
    require(approval.get('candidate_sha')==release['candidate_sha'],'Approval belongs to another candidate')


def validate_deployment(release, deployment):
    require(isinstance(deployment,dict),'Deployment evidence is missing')
    require(deployment.get('environment')=='production','Deployment target must be production')
    require(deployment.get('candidate_sha')==release['candidate_sha'],'Deployed source differs from approved candidate')
    for field in ('service','reference','verification'):
        require(nonempty(deployment.get(field)),f'Deployment {field} is required')


def validate_data(data):
    require(data.get('schema_version')==1,'Unsupported release register schema')
    releases=data.get('releases')
    require(isinstance(releases,list) and bool(releases),'Release records are missing')
    seen=set()
    for release in releases:
        identity=release.get('id')
        require(isinstance(identity,str) and re.fullmatch(r'SMP-\d{8}-\d{3}',identity),'Invalid release ID')
        require(identity not in seen,'Duplicate release ID');seen.add(identity)
        require(nonempty(release.get('title')) and nonempty(release.get('scope')),'Release title/scope is required')
        require(bool(SHA.fullmatch(release.get('candidate_sha',''))),'Full candidate commit SHA is required')
        require(release.get('state') in STATES,'Invalid release state')
        require(nonempty(release.get('rollback_plan')),'Rollback plan is required')
        require(isinstance(release.get('checks'),dict),'Release checks must be a mapping')
        if release['state'] != 'legacy_unverified':
            require(REQUIRED_GATES <= set(release['checks']),'Mandatory production gates cannot be removed')
        for check in release['checks'].values():
            require(check.get('status') in {'passed','failed','pending','blocked','not_run'},'Invalid check status')
            require(nonempty(check.get('evidence')),'Check evidence/status explanation is required')
        events=release.get('events')
        require(isinstance(events,list) and bool(events),'Release history is missing')
        previous=None
        for event in events:
            state=event.get('state')
            require(state in STATES,'Invalid event state')
            for field in ('actor','reference','summary'):
                require(nonempty(event.get(field)),f'Event {field} is required')
            timestamp(event.get('recorded_at'))
            require(bool(SHA.fullmatch(event.get('candidate_sha',''))),'Event candidate SHA is required')
            if previous:
                require(state in TRANSITIONS[previous],f'Invalid release transition: {previous} -> {state}')
            else:
                require(state in {'planned','blocked','legacy_unverified'},'Release must start planned, blocked, or historically unverified')
            if state=='approved':
                validate_approval({**release,'candidate_sha':event['candidate_sha']},event.get('approval'))
            if state=='deployed':
                validate_deployment({**release,'candidate_sha':event['candidate_sha']},event.get('deployment'))
            previous=state
        require(events[-1]['state']==release['state'],'Release state differs from last recorded event')
        if release['state'] in {'approved','deployment_started','deployed','deployment_failed','rolled_back'}:
            approvals=[e for e in events if e['state']=='approved' and e['candidate_sha']==release['candidate_sha']]
            require(bool(approvals),'No UAT approval for this exact candidate')
            validate_approval(release,approvals[-1]['approval'])
            passed(release)
    require(data.get('active_release_id') in seen,'Active release does not exist')
    return data


def active(data, identity=None):
    identity=identity or data['active_release_id']
    return next((release for release in data['releases'] if release['id']==identity),None)


def text(value):
    return str(value).replace('\n',' ').replace('|','\\|').replace('<','&lt;').replace('>','&gt;')


def render_history(data):
    lines=['',f"Active release: **{data['active_release_id']}**.",'']
    for release in data['releases']:
        lines += [f"### {release['id']} — {text(release['title'])}",'',
                  f"State: **{release['state']}** · Candidate: `{release['candidate_sha']}`",'',
                  text(release['scope']),'',f"Rollback: {text(release['rollback_plan'])}",'']
        for name,check in release.get('checks',{}).items():
            lines.append(f"- Check `{name}`: **{check['status']}** — {text(check.get('evidence','Not recorded'))}")
        if release.get('checks'):lines.append('')
        for event in release['events']:
            lines.append(f"- {event['recorded_at']} · **{event['state']}** · {text(event['actor'])}: {text(event['summary'])} Reference: {text(event['reference'])}. Source: `{event['candidate_sha']}`.")
            if event.get('approval'):
                approval=event['approval']
                lines.append(f"  Approval: {text(approval['approver'])}, {approval['approved_at']}, `{approval['decision']}`; reference: {text(approval['reference'])}.")
            if event.get('deployment'):
                deployment=event['deployment']
                lines.append(f"  Production: {text(deployment['service'])}; deployment: {text(deployment['reference'])}; verified: {text(deployment['verification'])}.")
        lines.append('')
    return '\n'.join(lines)


def master_parts(master):
    require(master.count(START)==1 and master.count(END)==1,'Master history markers are missing or duplicated')
    prefix,rest=master.split(START)
    history,suffix=rest.split(END)
    return prefix,history,suffix


def load(root):
    return validate_data(json.loads((root/REGISTER).read_text()))


def validate(root, require_current=False):
    for path in ('AGENTS.md',MASTER,'docs/RELEASE_POLICY.md'):
        require((root/path).is_file(),f'Read-before-change policy file is missing: {path}')
    data=load(root)
    _,history,_=master_parts((root/MASTER).read_text())
    require(history==render_history(data),'Master history is stale; run release_guard.py sync')
    if require_current:check_candidate(root,active(data))
    return data


def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True)
    require(result.returncode==0,'Unable to verify candidate Git content')
    return result.stdout


def check_candidate(root,release):
    sha=release['candidate_sha']
    require(git(root,'cat-file','-t',sha).strip()=='commit','Candidate is not a commit')
    changed=set(git(root,'diff','--name-only',sha,'--').splitlines())
    untracked=set(git(root,'ls-files','--others','--exclude-standard').splitlines())
    changed |= untracked
    if MASTER in changed:
        before=git(root,'show',sha+':'+MASTER)
        old_prefix,_,old_suffix=master_parts(before)
        prefix,_,suffix=master_parts((root/MASTER).read_text())
        require((prefix,suffix)==(old_prefix,old_suffix),'Master policy/facts changed after candidate freeze')
    changed -= {REGISTER,MASTER}
    require(not changed,'Candidate changed after freeze; retest and obtain new UAT approval: '+', '.join(sorted(changed)))


def production_ready(root,data,identity):
    release=active(data,identity)
    require(release is not None,'Release does not exist')
    require(release['state']=='approved','Production blocked: release has no current UAT approval')
    passed(release)
    check_candidate(root,release)
    return release


def atomic(path,contents):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as file:
        temporary=Path(file.name);file.write(contents)
    try:os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def sync(root,data):
    prefix,_,suffix=master_parts((root/MASTER).read_text())
    atomic(root/MASTER,prefix+START+render_history(data)+END+suffix)


def record(root,identity,event):
    data=deepcopy(validate(root))
    release=active(data,identity)
    require(release is not None,'Release does not exist')
    state=event.get('state')
    require(state in TRANSITIONS[release['state']],'Event is not allowed from the current release state')
    for field in ('actor','reference','summary'):
        require(nonempty(event.get(field)),f'Event {field} is required')
    if state in {'approved','deployment_started','deployed','deployment_failed','rolled_back'}:
        check_candidate(root,release)
    if 'checks' in event:
        require(set(event['checks']) <= set(release['checks']),'Cannot silently replace required checks')
        release['checks'].update(event.pop('checks'))
    if state=='approved':
        passed(release);validate_approval(release,event.get('approval'))
    if state=='deployment_started':
        production_ready(root,data,identity)
        require(nonempty(event.get('target')),'Production service/database target reference is required')
    if state=='deployed':validate_deployment(release,event.get('deployment'))
    if state=='rolled_back':
        require(nonempty(event.get('rollback_authorization')) and nonempty(event.get('verification')),
                'Rollback authorization and verification evidence are required')
    event={**event,'recorded_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
           'candidate_sha':release['candidate_sha']}
    release['events'].append(event);release['state']=state
    validate_data(data)
    atomic(root/REGISTER,json.dumps(data,indent=2)+'\n')
    sync(root,data)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['show','validate','sync','check-production','record'])
    parser.add_argument('--release')
    parser.add_argument('--require-current',action='store_true')
    parser.add_argument('--event-file',type=Path)
    args=parser.parse_args()
    if args.command=='sync':sync(ROOT,load(ROOT))
    elif args.command=='record':
        require(args.release and args.event_file,'Record requires --release and --event-file')
        record(ROOT,args.release,json.loads(args.event_file.read_text()))
    else:
        data=validate(ROOT,args.require_current)
        if args.command=='check-production':production_ready(ROOT,data,args.release or data['active_release_id'])
        elif args.command=='show':
            release=active(data,args.release)
            require(release is not None,'Release does not exist')
            print(f"Read AGENTS.md, APP_MASTER.md, docs/RELEASE_POLICY.md first.\n{release['id']}: {release['state']}\nCandidate: {release['candidate_sha']}")
    print('PASS: '+args.command)


if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        parser_message=str(exc)
        raise SystemExit('BLOCKED: '+parser_message)

"""Self-authored synthetic evidence. Evaluator labels are not shipped here."""
from .core import SimError

LICENSE = 'CC0-1.0; self-authored synthetic fixture text'
T1 = '2026-01-01T12:00:00+00:00'
T2 = '2026-01-02T12:00:00+00:00'
T3 = '2026-01-03T12:00:00+00:00'

CATALOG = {
    'river': {
        'title':'Synthetic river monitoring',
        'question':'As of January 2, what is known about the river level, the alert threshold, and Station North?',
        'pages':[
            ('https://river.example/bulletin',T1,'River bulletin','January 1 observation: Central gauge measured 42 cm. An alert requires 50 cm or more.'),
            ('https://river.example/bulletin',T2,'River bulletin','January 2 observation: Central gauge measured 47 cm. An alert requires 50 cm or more. Station North data were unavailable.'),
            ('https://river.example/bulletin',T3,'River bulletin','January 3 observation: Central gauge measured 54 cm. The 50 cm alert threshold was exceeded.'),
            ('https://river.example/method',T1,'Measurement method','All river observations are gauge height in centimetres. Daily observations do not establish the maximum between readings.')],
        'missing_url':'https://river.example/station-north'},
    'energy': {
        'title':'Synthetic energy trial',
        'question':'As of January 2, summarize the battery trial result, its sample size, and what is known about cold-weather performance.',
        'pages':[
            ('https://energy.example/trial',T1,'Trial preliminary note','A preliminary battery trial reported 80 Wh/kg. This was based on 4 cells tested at 20 degrees Celsius.'),
            ('https://energy.example/trial',T2,'Trial correction','The corrected battery trial result is 74 Wh/kg, based on 6 cells tested at 20 degrees Celsius. The preliminary 80 Wh/kg value is withdrawn.'),
            ('https://energy.example/trial',T3,'Cold chamber report','A separate January 3 cold chamber trial at minus 10 degrees Celsius measured 51 Wh/kg across 3 cells.'),
            ('https://energy.example/scope',T1,'Scope note','Room-temperature tests do not establish cold-weather performance. Cold chamber results are not included in this report.')],
        'missing_url':'https://energy.example/field-lifetime'},
    'civic': {
        'title':'Synthetic council project',
        'question':'As of January 2, what has the council approved, how much is budgeted, and is the project complete?',
        'pages':[
            ('https://civic.example/minutes',T1,'Council draft minutes','The draft proposes a footbridge with a budget ceiling of 2 million credits. No vote has occurred.'),
            ('https://civic.example/minutes',T2,'Council approved minutes','On January 2 the council approved the footbridge design phase by 6 votes to 3, with a ceiling of 1.5 million credits. Construction was not authorized.'),
            ('https://civic.example/minutes',T3,'Council construction notice','On January 3 the council separately authorized construction, with a total ceiling of 2.2 million credits.'),
            ('https://civic.example/status',T1,'Project status','The project is in planning. No completion certificate has been issued.')],
        'missing_url':'https://civic.example/completion-certificate'},
}


def list_starters():
    return [{'slug':slug,'title':v['title'],'question':v['question'],'synthetic':True,'source_license':LICENSE}
            for slug,v in CATALOG.items()]


def install_starter(store,slug,*,budgets=None):
    """Install evidence with an explicit simulated archive clock.

    Uses a second Store handle with controlled timestamps; never changes the
    caller's clock. These are synthetic historical events, not live captures.
    """
    if slug not in CATALOG: raise SimError('unknown_starter','Choose river, energy or civic.')
    from .core import Store
    item=CATALOG[slug]
    at=[T1]
    staging=Store(store.root,clock=lambda:at[0])
    world=staging.create_world(item['question'],budgets)
    documents=[]
    for url,when,title,text in sorted(item['pages'],key=lambda p:(p[1],p[0])):
        at[0]=when
        doc=staging.add_capture(world['id'],url=url,body=text.encode(),text=text,title=title,captured_at=when,
            headers={'content-type':'text/plain; charset=utf-8'},links=[item['missing_url']],
            provenance={'kind':'self_authored_synthetic','license':LICENSE,'starter_version':'1','slug':slug})
        documents.append({'id':doc['id'],'url':url,'captured_at':when})
    at[0]=T3
    snapshots=[{'label':label,**staging.freeze(world['id'],when)} for label,when in [('day1',T1),('day2',T2),('day3',T3)]]
    return {'world_id':world['id'],'slug':slug,'synthetic':True,'source_license':LICENSE,
            'snapshots':snapshots,'default_snapshot_id':snapshots[1]['id'],'documents':documents,
            'notice':'Synthetic evidence only. Connect a real LLM or native agent to research and compare it.'}

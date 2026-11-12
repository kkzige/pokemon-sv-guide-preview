#!/usr/bin/env python3
"""Build a reproducible, fully local SV *basic reference snapshot* from PokeAPI CSV.

This does NOT claim full material locations, event raids, item availability, or game-specific
mechanics. GitHub Actions runs it online before publishing the PWA, and fails closed if
key tables are missing. stdlib only. Use --fixture for deterministic offline CI smoke test.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, io, json, pathlib, sys, time, urllib.request
from collections import defaultdict

ROOT='https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/'
FALLBACK='https://cdn.jsdelivr.net/gh/PokeAPI/pokeapi@master/data/v2/csv/'
FILES=['pokemon_species','pokemon_species_names','pokemon_egg_groups','pokemon_dex_numbers','pokemon','pokemon_types','pokemon_stats','pokemon_abilities','abilities','ability_names','moves','move_names','pokemon_moves','version_groups','items','item_names']
TYPES=dict(enumerate(['normal','fighting','flying','poison','ground','rock','bug','ghost','steel','fire','water','grass','electric','psychic','ice','dragon','dark','fairy'],1))
STATS={1:0,2:1,3:2,4:3,5:4,6:5}
EGGS={1:'monster',2:'water1',3:'bug',4:'flying',5:'field',6:'fairy',7:'grass',8:'humanlike',9:'water3',10:'mineral',11:'amorphous',12:'water2',13:'ditto',14:'dragon',15:'undiscovered'}
DEXES={31:'帕底亚',32:'北上乡',33:'蓝莓'}
# These are three distinct game Pokédexes, with overlapping species.
EXPECTED={'帕底亚':400,'北上乡':200,'蓝莓':243}

def n(v,default=0):
    try:return int(v)
    except (TypeError,ValueError):return default

def get_text(name):
    errors=[]
    for host in (ROOT,FALLBACK):
        for attempt in range(3):
            try:
                req=urllib.request.Request(host+name+'.csv',headers={'User-Agent':'SV-Tactical-Guide-DataBuilder/1.2','Accept':'text/csv'})
                with urllib.request.urlopen(req,timeout=45) as resp:
                    data=resp.read()
                if len(data)<15:raise ValueError('unexpected empty csv')
                return data.decode('utf-8-sig')
            except Exception as exc:
                errors.append(str(exc))
                time.sleep(min(.6*(attempt+1),2))
    raise RuntimeError(f'Cannot download {name}.csv: {"; ".join(errors[-3:])}')

def csv_rows(content):return list(csv.DictReader(io.StringIO(content)))

def load_online():
    result={};checksums={}
    for name in FILES:
        print('Downloading',name,flush=True)
        content=get_text(name)
        checksums[name]=hashlib.sha256(content.encode('utf-8')).hexdigest()
        result[name]=csv_rows(content)
        if not result[name]:raise ValueError(f'Empty table {name}')
    return result,checksums

def localized(rows,key='pokemon_species_id'):
    return {n(r[key]):r['name'] for r in rows if n(r.get('local_language_id'))==12 and r.get('name')}

def build(tables,checksums,strict=True):
    # Detect upstream schema drift before generating incorrect lookups.
    schema={'pokemon_species':['id','identifier','evolves_from_species_id','gender_rate'],
      'pokemon_dex_numbers':['species_id','pokedex_id'],
      'pokemon':['id','identifier','species_id','is_default'],
      'pokemon_types':['pokemon_id','type_id','slot'],
      'pokemon_stats':['pokemon_id','stat_id','base_stat'],
      'pokemon_abilities':['pokemon_id','ability_id','is_hidden'],
      'pokemon_moves':['pokemon_id','version_group_id','move_id','pokemon_move_method_id','level'],
      'moves':['id','identifier','type_id','damage_class_id'],
      'version_groups':['id','identifier']}
    for table,required in schema.items():
        keys=set(tables[table][0]);missing=set(required)-keys
        if missing:raise ValueError(f'{table}.csv schema changed; missing {sorted(missing)}')
    species={n(r['id']):r for r in tables['pokemon_species']}
    species_zh=localized(tables['pokemon_species_names'])
    abilities={n(r['id']):r['identifier'] for r in tables['abilities']}
    ability_zh=localized(tables['ability_names'],'ability_id')
    moves_raw={n(r['id']):r for r in tables['moves']}
    move_zh=localized(tables['move_names'],'move_id')
    items_raw={n(r['id']):r['identifier'] for r in tables['items']}
    item_zh=localized(tables['item_names'],'item_id')
    dex=defaultdict(list);regional=defaultdict(set)
    for r in tables['pokemon_dex_numbers']:
        s=n(r['species_id']);d=DEXES.get(n(r['pokedex_id']))
        if d and s in species:
            dex[s].append(d);regional[d].add(s)
    counts={key:len(regional[key]) for key in EXPECTED}
    if strict and any(counts[k]<v for k,v in EXPECTED.items()):
        raise ValueError(f'REGIONAL COVERAGE GATE FAILED: {counts}, expected at least {EXPECTED}; PokeAPI snapshot may be incomplete')
    if not any('scarlet-violet'==r['identifier'] for r in tables['version_groups']):
        raise ValueError('No scarlet-violet version group found; refuse cross-generation learnsets')
    vgids={n(r['id']) for r in tables['version_groups'] if r['identifier']=='scarlet-violet'}
    if len(vgids)!=1:raise ValueError('Ambiguous scarlet-violet version group')
    sv_move_rows=[r for r in tables['pokemon_moves'] if n(r['version_group_id']) in vgids]
    if strict and len(sv_move_rows)<8000:raise ValueError(f'SV move learnset unexpectedly small: {len(sv_move_rows)}')
    groups=defaultdict(list)
    for r in tables['pokemon_egg_groups']:
        group=EGGS.get(n(r['egg_group_id']))
        if group:groups[n(r['species_id'])].append(group)
    poke_types=defaultdict(list);poke_stats=defaultdict(lambda:[None]*6);poke_abilities=defaultdict(list)
    for r in tables['pokemon_types']:
        t=TYPES.get(n(r['type_id']))
        if t:poke_types[n(r['pokemon_id'])].append((n(r['slot']),t))
    for r in tables['pokemon_stats']:
        pos=STATS.get(n(r['stat_id']))
        if pos is not None:poke_stats[n(r['pokemon_id'])][pos]=n(r['base_stat'])
    for r in tables['pokemon_abilities']:
        aid=n(r['ability_id']);name=abilities.get(aid)
        if name:poke_abilities[n(r['pokemon_id'])].append((n(r.get('slot')),name,n(r['is_hidden'])==1))
    poke_moves=defaultdict(dict);valid_moves=set()
    for r in sv_move_rows:
        pid=n(r['pokemon_id']);mid=n(r['move_id']);move=moves_raw.get(mid)
        if not move:continue
        valid_moves.add(mid)
        name=move['identifier'];method=n(r['pokemon_move_method_id']);level=n(r.get('level'))
        entry=poke_moves[pid].setdefault(name,[])
        tag=f'{method}:{level}' if method==1 else str(method)
        if tag not in entry:entry.append(tag)
    moves={}
    for mid in sorted(valid_moves):
        r=moves_raw[mid];id=r['identifier'];type_id=n(r['type_id']);type_name=TYPES.get(type_id)
        if not type_name:continue
        moves[id]={'zh':move_zh.get(mid,id),'type':type_name,'category':{1:'status',2:'physical',3:'special'}.get(n(r['damage_class_id']),'status'),
           'power':n(r['power']), 'accuracy':n(r['accuracy'],None) if r['accuracy'] else None,'pp':n(r['pp']),'id':mid,'scope':'sv-learnset'}
    if strict and len(moves)<350:raise ValueError(f'SV move catalog unexpectedly small: {len(moves)}')
    root_cache={}
    def base_of(sid):
        if sid in root_cache:return root_cache[sid]
        seen=set();cur=sid
        while cur in species and cur not in seen:
            seen.add(cur);parent=n(species[cur]['evolves_from_species_id'])
            if not parent or parent not in species:break
            cur=parent
        root_cache[sid]=cur;return cur
    pokes=[];preferred=defaultdict(list)
    for p in tables['pokemon']:
        pid=n(p['id']);sid=n(p['species_id']);sp=species.get(sid)
        if not sp:continue
        if sid not in dex and pid not in poke_moves:continue
        # Non-default forms without explicit SV learnset evidence may be Mega/other-game forms.
        if n(p['is_default'])!=1 and pid not in poke_moves:continue
        if not poke_types.get(pid) or None in poke_stats[pid]:continue
        first=base_of(sid)
        status='regional-dex' if sid in dex else 'sv-learnset-only'
        # A Pokemon ID is not always a National Pokedex species ID (e.g. alternative form).
        obj={'id':sid,'pokemonId':pid,'en':p['identifier'],'zh':species_zh.get(sid,sp['identifier']),
             'speciesEn':sp['identifier'],'types':[x[1] for x in sorted(poke_types[pid])],
             'stats':poke_stats[pid],'abilities':[x[1] for x in sorted(poke_abilities[pid])],
             'hiddenAbilities':[x[1] for x in poke_abilities[pid] if x[2]],
             'eggGroups':sorted(set(groups.get(sid,[]))),'genderRate':n(sp['gender_rate'],-1),
             'evolvesFrom':species.get(n(sp['evolves_from_species_id']),{}).get('identifier',''),
             'baseEn':species[first]['identifier'],'baseZh':species_zh.get(first,species[first]['identifier']),
             'svDex':dex.get(sid,[]),'scope':status,'isDefault':n(p['is_default'])==1,
             'moves':list(poke_moves.get(pid,{}).keys()),'learnset':poke_moves.get(pid,{})}
        pokes.append(obj);preferred[sid].append(obj)
    # Fix species-specific form alias collisions: display one canonical species for a Chinese-name search,
    # but preserve named alternate forms for exact English/form lookup.
    directory=[]
    for sid,entries in sorted(preferred.items()):
        main=next((x for x in entries if x['isDefault']),entries[0]);
        directory.append({'id':sid,'en':main['en'],'zh':main['zh'],'svDex':main['svDex']})
    index=[]
    for sid,r in sorted(species.items()):
        root=base_of(sid)
        index.append({'id':sid,'en':r['identifier'],'zh':species_zh.get(sid,r['identifier']),
          'eggGroups':sorted(set(groups.get(sid,[]))),'genderRate':n(r['gender_rate'],-1),
          'svDex':dex.get(sid,[]),'baseEn':species[root]['identifier'],
          'baseZh':species_zh.get(root,species[root]['identifier']),
          'evolvesFrom':species.get(n(r['evolves_from_species_id']),{}).get('identifier','')})
    ability_ref=[{'id':name,'zh':ability_zh.get(aid,name),'scope':'cross-generation','remote':True} for aid,name in sorted(abilities.items())]
    item_ref=[{'id':name,'zh':item_zh.get(iid,name),'scope':'cross-generation','remote':True} for iid,name in sorted(items_raw.items())]
    if strict and len(pokes)<500:raise ValueError(f'SV Pokemon form dataset unexpectedly small: {len(pokes)}')
    # Strong invariants that can be tested without editorial labels.
    if strict and not any(x['en']=='pikachu' and x['baseEn']=='pichu' for x in pokes):
        raise ValueError('Evolution family invariant failed (Pikachu → Pichu)')
    coverage={'regionSpecies':counts,'regionUnion':len(set().union(*regional.values())),
      'pokemonForms':len(pokes),'pokemonSpeciesSearchable':len(directory), 'speciesBreedingIndex':len(index),
      'svMoveEntries':len(moves),'svLearnsetRows':len(sv_move_rows),
      'abilitiesCrossGeneration':len(ability_ref),'itemsCrossGeneration':len(item_ref),
      'manualVerifiedMaterials':13,'manualVerifiedItemLocations':34,
      'scopeNote':'图鉴/招式来自PokeAPI公开数据；地区图鉴与招式学习记录分开标记。跨世代道具/特性条目不证明朱紫可获得。地点/机制仅人工精选、尚非全量。'}
    date=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    return {'schema':1,'generatedAt':date,'source':'PokeAPI/pokeapi master csv','sourceSHA256':checksums,
      'coverage':coverage,'pokemon':pokes,'directory':directory,'breedingIndex':index,'moves':moves,
      'abilityIndex':ability_ref,'itemIndex':item_ref}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='sv-data.json');ap.add_argument('--coverage',default='coverage.json');ap.add_argument('--fixture',help='Directory containing pre-saved CSV files, for offline tests');ap.add_argument('--no-strict',action='store_true');args=ap.parse_args()
    if args.fixture:
        base=pathlib.Path(args.fixture);tables={x:csv_rows((base/(x+'.csv')).read_text('utf-8-sig')) for x in FILES};checksums={x:'fixture' for x in FILES}
    else:tables,checksums=load_online()
    data=build(tables,checksums,strict=not args.no_strict)
    out=pathlib.Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    cov=pathlib.Path(args.coverage);cov.parent.mkdir(parents=True,exist_ok=True)
    cov.write_text(json.dumps({'version':'1.2.0','generatedAt':data['generatedAt'],**data['coverage']},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data['coverage'],ensure_ascii=False,indent=2));print('Saved',out,'bytes',out.stat().st_size)
if __name__=='__main__':main()

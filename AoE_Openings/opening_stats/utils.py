import time
import math
import gc
import os
import json
import urllib

from django.db.models import F, Count, Case, When, Q, Sum, Avg, Value, FloatField
from .AoE_Rec_Opening_Analysis.aoe_replay_stats import OpeningType
from opening_stats.models import Matches, Techs, MatchPlayerActions, CivEloWins, OpeningEloWins, OpeningEloTechs, Patches, AdvancedQueryQueue, AdvancedQueryResults
import django.utils.timezone

ELO_DELTA = 50

TIME_BUCKET_DELTA = 25000 #millis

ADVANCED_QUERY_COUNT = 50

Updated_Tech_Names = {
  101:'Feudal Age',
  102:'Castle Age'
}

#Need to update all bucket tables after modifying these
Basic_Strategies = [
    #General Openings
    [
        "Premill_Drush_Any", [OpeningType.PremillDrush.value],
        []
    ],
    [
        "Postmill_Drush_Any", [OpeningType.PostmillDrush.value],
        []
    ],
    [
        "MAA_Any",
        [OpeningType.Maa.value],
        [OpeningType.AnyDrush.value]
    ],
    [
        "Scouts_Any", [OpeningType.FeudalScoutOpening.value],
        []
    ],
    [
        "Range_Opener_Any",
        [
            OpeningType.FeudalArcherOpening.value,
            OpeningType.FeudalSkirmOpening.value
        ], []
    ],
]

#Need to update all bucket tables after modifying these
Followups = [
    #Specific Openings and followups
    [
        "Premill_Drush_FC", [OpeningType.PremillDrushFC.value],
        []
    ],
    [
        "Postmill_Drush_FC", [OpeningType.PostmillDrushFC.value],
        []
    ],
    [
        "Premill_Drush_Range_Followup",
        [
            OpeningType.PremillDrushArchers.value,
            OpeningType.PremillDrushSkirms.value
        ], [OpeningType.FeudalScoutFollowup.value]
    ],  #disallow scouts
    [
        "Postmill_Drush_Range_Followup",
        [
            OpeningType.PostmillDrushArchers.value,
            OpeningType.PostmillDrushSkirms.value
        ], [OpeningType.FeudalScoutFollowup.value]
    ],  #disallow scouts
    [
        "Scouts_No_Feudal_Followup", [OpeningType.FeudalScoutOpening.value],
        [
            OpeningType.FeudalArcherFollowup.value,
            OpeningType.FeudalSkirmFollowup.value
        ]
    ],
    [
        "Scouts_Range_Followup",
        [OpeningType.ScoutsArchers.value, OpeningType.ScoutsSkirms.value],
        []
    ],
    [
        "MAA_No_Feudal_Followup", [OpeningType.Maa.value],
        [
            OpeningType.AnyDrush.value,
            OpeningType.FeudalArcherFollowup.value,
            OpeningType.FeudalSkirmFollowup.value,
            OpeningType.FeudalScoutFollowup.value
        ]
    ],
    [
        "MAA_Range_Followup",
        [OpeningType.MaaSkirms.value, OpeningType.MaaArchers.value],
        [OpeningType.AnyDrush.value, OpeningType.FeudalScoutFollowup.value]
    ],
    #ALWAYS MAKE SURE THESE ARE THE LAST 2 OPENINGS
    [
        "Straight_FC",
        [OpeningType.FastCastle.value],
        [OpeningType.AnyDrush.value,
         OpeningType.FeudalScoutOpening.value,
         OpeningType.Maa.value,
         OpeningType.FeudalArcherOpening.value,
         OpeningType.FeudalSkirmOpening.value]
    ],
    [
        "Unknown",
        #never triggers, manually set
        [OpeningType.Unused.value
        ], [OpeningType.Unused.value]
    ],
]

OPENINGS = Basic_Strategies + Followups

CIV_IDS_TO_NAMES = {}

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'AoE_Rec_Opening_Analysis', 'aoe2techtree', 'data','data.json')) as json_file:
  aoe_data = json.load(json_file)
  for name, value in aoe_data["civ_names"].items():
    CIV_IDS_TO_NAMES[int(value) - 10270] = name




def data_dict_to_query_string(data):
  string = ""
  #sort alpha
  sorted_keys = sorted(data.keys(), key=lambda x:x.lower())
  for key in sorted_keys:
    string += f'{key}='
    if type(data[key]) is list:
      string += ','.join([str(i) for i in sorted(data[key])])
    else:
      string += str(data[key])
    string += '&'
  return string

def query_string_to_data_dict(string):
  ret_dict = dict(urllib.parse.parse_qsl(string))
  #fix data to lists for relevant fields
  for k,v in ret_dict.items():
    if (k == 'max_elo' or
        k == 'min_elo' or
        k == 'exclude_mirrors' or
        k == 'exclude_civ_mirrors' or
        k == 'exclude_opening_mirrors' or
        k == "left_player_id"):
      continue
    #rest are lists
    ret_dict[k] = [int(i) for i in v.split(",")]
  return ret_dict


def parse_standard_query_parameters(request, default_exclude_mirrors) :
  data = {}
  error_code = False
  data['min_elo'] = int(request.GET.get('min_elo', "0").split(",")[0])
  data['max_elo'] = int(request.GET.get('max_elo', "9000").split(",")[0])
  data['exclude_mirrors'] = request.GET.get('exclude_mirrors', str(default_exclude_mirrors)).split(",")[0].lower() == "true"
  data['include_ladder_ids'] = list(map(int, request.GET.get('include_ladder_ids', "-1").split(",")))
  #default to newest patch if none selected
  data['include_patch_ids'] = list(map(int, request.GET.get('include_patch_ids', str(Patches.objects.all().last().id)).split(",")))

  data['include_map_ids'] = list(map(int, request.GET.get('include_map_ids', "-1").split(",")))
  data['include_civ_ids'] = list(map(int, request.GET.get('include_civ_ids', "-1").split(",")))
  data['exclude_civ_ids'] = list(map(int, request.GET.get('exclude_civ_ids', "-1").split(",")))
  data['clamp_civ_ids'] = list(map(int, request.GET.get('clamp_civ_ids', "-1").split(",")))
  data['include_opening_ids'] = list(map(int, request.GET.get('include_opening_ids', "-1").split(",")))
  data['include_tech_ids'] = list(map(int, request.GET.get('include_tech_ids', "-1").split(",")))
  data['include_player_ids'] = list(map(int, request.GET.get('include_player_ids', "-1").split(",")))

  #Now validate data
  if data['min_elo'] < 0 or data['min_elo'] > 9000 or data['min_elo'] % 25:
    error_code = 400
  if data['max_elo'] < 0 or data['max_elo'] > 9000 or data['max_elo'] % 25:
    error_code = 400
  #TODO Add more db level validations
  return data, error_code

def check_list_of_ints(value):
  if not isinstance(value, list):
    return False
  if not all(isinstance(item, int) for item in value):
    return False
  return True

def parse_advanced_post_parameters(request, default_exclude_mirrors) :
  data = {}
  error_code = False
  data['min_elo'] = request.data.get('min_elo', 0)
  data['max_elo'] = request.data.get('max_elo', 3000)
  data['left_player_id'] = request.data.get('left_player_id', 0)
  data['exclude_civ_mirrors'] = request.data.get('exclude_civ_mirrors', False)
  data['exclude_opening_mirrors'] = request.data.get('exclude_opening_mirrors', False)

  data['include_ladder_ids'] = request.data.get('include_ladder_ids', [-1])
  error_code = False if check_list_of_ints(data['include_ladder_ids']) else 400
  #default to newest patch if none selected
  data['include_patch_ids'] = request.data.get('include_patch_ids', [-1])
  error_code = False if check_list_of_ints(data['include_patch_ids']) else 400

  data['include_map_ids'] = request.data.get('include_map_ids', [-1])
  error_code = False if check_list_of_ints(data['include_map_ids']) else 400

  #Allow up to 3 sets of players per query
  for i in range(ADVANCED_QUERY_COUNT*2):
    data[f'include_civ_ids_{i}'] = request.data.get(f'include_civ_ids_{i}', [-1])
    error_code = False if check_list_of_ints(data[f'include_civ_ids_{i}']) else 400
    data[f'include_opening_ids_{i}'] = request.data.get(f'include_opening_ids_{i}', [-1])
    error_code = False if check_list_of_ints(data[f'include_opening_ids_{i}']) else 400
    # Limit all inputs here to a single civ and opening id because it doesnt make sense otherwise
    if not error_code and len(data[f'include_civ_ids_{i}']) > 1:
      error_code = 400
    if not error_code and len(data[f'include_opening_ids_{i}']) > 1:
      error_code = 400
    #break out early if error code observed
    if error_code:
      break
  #combo params - only used for rebuilding the state
  data[f'include_left_civ_combinations'] = request.data.get(f'include_left_civ_combinations', [-1])
  error_code = False if check_list_of_ints(data[f'include_left_civ_combinations']) else 400
  data[f'include_left_opening_combinations'] = request.data.get(f'include_left_opening_combinations', [-1])
  error_code = False if check_list_of_ints(data[f'include_left_opening_combinations']) else 400
  data[f'include_right_civ_combinations'] = request.data.get(f'include_right_civ_combinations', [-1])
  error_code = False if check_list_of_ints(data[f'include_right_civ_combinations']) else 400
  data[f'include_right_opening_combinations'] = request.data.get(f'include_right_opening_combinations', [-1])
  error_code = False if check_list_of_ints(data[f'include_right_opening_combinations']) else 400

  #Now validate data
  if not isinstance(data['min_elo'], int):
    error_code = 400
  if not isinstance(data['max_elo'], int):
    error_code = 400
  if not isinstance(data['left_player_id'], int):
    error_code = 400
  if not isinstance(data['exclude_civ_mirrors'], bool):
    error_code = 400
  if not isinstance(data['exclude_opening_mirrors'], bool):
    error_code = 400
  #early return if anything is the wrong type
  if error_code:
    return data, error_code

  #value checking
  if data['min_elo'] < 0 or data['min_elo'] > 9000 or data['min_elo'] % 25:
    error_code = 400
  if data['max_elo'] < 0 or data['max_elo'] > 9000 or data['max_elo'] % 25:
    error_code = 400
  #TODO Add more db level validations
  return data, error_code

def count_response_to_dict(sql_response) :
  data = {}
  for key, value in sql_response.items():
    # keys are of format civ_victoryType, so split into nested dict because nicer
    # deal with total later
    if key != 'total':
      components = key.split("____")[0] #Remove anything after quad underscore, its just extra stuff for uniqueness
      components = components.split("_")
      name = " ".join(components[:-1])
      type = components[-1]
      if not name in data:
        data[name] = {}
      data[name][type] = value
      data[name]["name"] = name
  # go through each name and append a valid image field if relevant
  for key in data.keys():
    components = key.split(" vs ")
    left = components[0]
    right = components[1] if len(components) > 1 else ""
    data[key]["left_image"] = ""
    data[key]["right_image"] = ""
    #inefficient to do it this way but it is what it is, fix someday O(n^2)
    for value in CIV_IDS_TO_NAMES.values():
      if value in left:
        data[key]["left_image"] = f'{value.lower()}.png'
      if value in right:
        data[key]["right_image"] = f'{value.lower()}.png'
  return list(data.values())

def count_tech_response_to_dict(sql_response, aoe_data) :
  data = {}
  for key, value in sql_response.items():
    # keys are of format type__tech_name__id, so split into nested dict because nicer
    # deal with total later
    if key != 'total':
      components = key.split("__")
      name = components[0].replace('_',' ')
      type = components[1].replace('_',' ')
      tech_id = components[2].replace('_',' ')
      if not name in data:
        data[name] = {}
      data[name][type] = value  + Techs.objects.filter(id=tech_id).first().duration * 1000 if value is not None else value
      data[name]["name"] = name
  return list(data.values())

#When doing versus matches we only calculate each matchup once, so mirror matchups to make it easier to view for the end user
def mirror_vs_dict_names(data_list) :
  for i in range(len(data_list)):
    dict2 = data_list[i].copy() #make copy of the current dict, change name and reinsert
    #switch win rate
    if 'wins' in dict2 and 'total' in dict2 and dict2['wins'] is not None and dict2['total'] is not None:
      dict2['wins'] = dict2['total'] - data_list[i]['wins']
    old_name = data_list[i]['name']
    #split on vs
    components = old_name.split("vs")
    name1 = " ".join(filter(None, components[0].split(" "))) #handy trick to fix formatting
    name2 = " ".join(filter(None, components[1].split(" "))) #handy trick to fix formatting
    if name1 == name2: #dont duplicate mirrors, and also set winrate to -1 and let the front end handle it
      data_list[i]['wins'] = -1
      continue
    dict2['name'] = name2 + ' vs ' + name1
    data_list.append(dict2)

def opening_ids_to_openings_list(opening_ids):
  total_openings = Basic_Strategies + Followups
  openings = [total_openings[i] for i in opening_ids]
  return openings

def EnqueueOrCheckAdvancedRequest(data):
  query = data_dict_to_query_string(data)
  adv_query = AdvancedQueryQueue.objects.filter(query=query, stale=False).first()
  if adv_query is None:
    #doesnt exist add a new one to the queue
    adv_query = AdvancedQueryQueue(query=query)
    adv_query.save()
  else:
    if adv_query.result is not None:
      return adv_query.result.id
  #now report depth in queue or return result id if complete
  ids_in_queue = AdvancedQueryQueue.objects.filter(stale=False, result__isnull=True).order_by('id').values('id')
  ids_in_queue = [i['id'] for i in ids_in_queue]
  position_in_queue = list(ids_in_queue).index(adv_query.id)
  return position_in_queue

def ProcessNextElementInAdvancedQueue():
  start = time.time()
  adv_query = AdvancedQueryQueue.objects.filter(stale=False, result__isnull=True).order_by('id').values('id', 'query').first()
  if adv_query is None:
    #Nothing to process, queue is empty
    return False
  # process element in queue
  data = query_string_to_data_dict(adv_query['query'])
  aggregate_string = "Matches.objects"
  aggregate_string += generate_filter_statements_from_parameters(data, elo_string="average_elo")
  aggregate_string += generate_aggregate_statements_for_advanced_queue(data)
  matches = eval(aggregate_string)
  #we have a valid search result, so update the queue element with the result
  result = AdvancedQueryResults(data=matches)
  result.save()
  #get queue object so we can update with new foreign key
  adv_query =  AdvancedQueryQueue.objects.get(pk=adv_query['id'])
  adv_query.time_completed = django.utils.timezone.now()
  adv_query.result = result
  adv_query.save()
  end = time.time()
  print(end - start)
  return True

def generate_q_parameters_for_player(player_id, opening_ids, civ_ids, profile_id):
  #remove any invalid sets
  if opening_ids == [-1]:
    opening_ids.clear()
  if civ_ids == [-1]:
    civ_ids.clear()
   #if no valid sets return
  if not len(opening_ids) and not len(civ_ids) and not profile_id:
    return ""
  ret_string = "("
  if profile_id:
    ret_string += f'Q(player{player_id}_id={profile_id})'
    if opening_ids or civ_ids:
      ret_string += '&'
    else:
      #no other params so close and return
      ret_string += ")"
      return ret_string
  ret_string += "(("
  for opening_id in opening_ids:
    ret_string += "("
    opening = OPENINGS[opening_id]
    inclusions = opening[1]
    exclusions = opening[2]
    if not len(exclusions):
      exclusions = exclusions + [OpeningType.Unused.value]
    #convert inclusions to flags
    for i in range(len(inclusions)):
      ret_string += '('
      found = False
      for j in range(32):
        if inclusions[i] & 2**j:
          if found:
            ret_string += '&'
          found = True
          ret_string += f'Q(player{player_id}_opening_flag{j}=True)'
      # close inclusion
      ret_string += ')'
      if i < len(inclusions) - 1:
        ret_string += '|'
    # close inclusions
    ret_string += ')&('

    #convert exclusions to flags
    for i in range(len(exclusions)):
      ret_string += '('
      found = False
      for j in range(32):
        if exclusions[i] & 2**j:
          if found:
            ret_string += '&'
          found = True
          ret_string += f'Q(player{player_id}_opening_flag{j}=False)'
      # close inclusion
      ret_string += ')'
      if i < len(exclusions) - 1:
        ret_string += '&'
    # close exclusion
    ret_string += ')'
    if not opening_id == opening_ids[-1]:
      ret_string += '&'
  if opening_ids and civ_ids:
    #close openings
    ret_string += ')&('
  if civ_ids:
    for civ_id in civ_ids:
      ret_string += f'Q(player{player_id}_civilization={civ_id})'
      if not civ_id == civ_ids[-1]:
        ret_string += '|'

  ret_string += ')))'
  return ret_string


def civ_and_opening_ids_to_string(civ_ids, opening_ids) :
  ret_string = ""
  if len(civ_ids):
    if len(opening_ids):
      ret_string += f'{CIV_IDS_TO_NAMES[civ_ids[0]]}_{OPENINGS[opening_ids[0]][0]}'
    else:
      ret_string += f'{CIV_IDS_TO_NAMES[civ_ids[0]]}'
  elif len(opening_ids):
    ret_string += f'{OPENINGS[opening_ids[0]][0]}'
  return ret_string



def generate_aggregate_statements_for_advanced_queue(data):
  aggregate_string = f'.aggregate('
  for i in range(0, ADVANCED_QUERY_COUNT*2, 2):
    #Skip row if it doesnt have all data and give blank array so we dont hit a value not exists error
    if f'include_opening_ids_{i}' not in data and f'include_civ_ids_{i}' not in data and f'include_opening_ids_{i+1}' not in data and f'include_civ_ids_{i+1}' not in data:
      continue
    if f'include_opening_ids_{i}' not in data:
      data[f'include_opening_ids_{i}'] = []
    if f'include_civ_ids_{i}' not in data:
      data[f'include_civ_ids_{i}'] = []
    if f'include_opening_ids_{i+1}' not in data:
      data[f'include_opening_ids_{i+1}'] = []
    if f'include_civ_ids_{i+1}' not in data:
      data[f'include_civ_ids_{i+1}'] = []
    #enforce that all lists are length 1! - cant just use the validator, need to enforce
    if len(data[f'include_opening_ids_{i}']) > 1:
      data[f'include_opening_ids_{i}'] = [data[f'include_opening_ids_{i}'][0]]
    if len(data[f'include_civ_ids_{i}']) > 1:
      data[f'include_civ_ids_{i}'] = [data[f'include_civ_ids_{i}'][0]]
    if len(data[f'include_opening_ids_{i+1}']) > 1:
      data[f'include_opening_ids_{i+1}'] = [data[f'include_opening_ids_{i+1}'][0]]
    if len(data[f'include_civ_ids_{i+1}']) > 1:
      data[f'include_civ_ids_{i+1}'] = [data[f'include_civ_ids_{i+1}'][0]]
    profile_id = int(data.get('left_player_id',0))
    if (data[f'include_opening_ids_{i}'] == [-1]
        and data[f'include_civ_ids_{i}'] == [-1]
        and data[f'include_opening_ids_{i+1}'] == [-1]
        and data[f'include_civ_ids_{i+1}'] == [-1]):
      #if neither has any selections, skip
      continue
    left_strings_p1 = generate_q_parameters_for_player(1, data[f'include_opening_ids_{i}'], data[f'include_civ_ids_{i}'], profile_id)
    left_strings_p2 = generate_q_parameters_for_player(2, data[f'include_opening_ids_{i}'], data[f'include_civ_ids_{i}'], profile_id)
    right_strings_p1 = generate_q_parameters_for_player(1, data[f'include_opening_ids_{i+1}'], data[f'include_civ_ids_{i+1}'], 0)
    right_strings_p2 = generate_q_parameters_for_player(2, data[f'include_opening_ids_{i+1}'], data[f'include_civ_ids_{i+1}'], 0)

    # Remove opening mirrors
    if 'exclude_opening_mirrors' in data and data['exclude_opening_mirrors'] == 'True':
      left_strings_openings_p1 = generate_q_parameters_for_player(1, data[f'include_opening_ids_{i}'], [], 0)
      left_strings_openings_p2 = generate_q_parameters_for_player(2, data[f'include_opening_ids_{i}'], [], 0)
      if left_strings_openings_p1:
        if right_strings_p1:
          right_strings_p1 += '&'
        right_strings_p1 += '~(' + left_strings_openings_p1 + ')'
      if left_strings_openings_p2:
        if right_strings_p2:
          right_strings_p2 += '&'
        right_strings_p2 += '~(' + left_strings_openings_p2 + ')'

    matchup_name = civ_and_opening_ids_to_string(data[f'include_civ_ids_{i}'], data[f'include_opening_ids_{i}'])
    matchup_name += '__vs__'
    matchup_name += civ_and_opening_ids_to_string(data[f'include_civ_ids_{i+1}'], data[f'include_opening_ids_{i+1}'])
    suffix = f'___{i}'
    #total matches
    aggregate_string += f'{matchup_name}_total_{suffix}=Count(Case(When('
    if left_strings_p1:
      aggregate_string += left_strings_p1
    if left_strings_p1 and right_strings_p2:
      aggregate_string += '&'
    if right_strings_p2:
      aggregate_string += right_strings_p2
    aggregate_string += ',then=1),'
    #close when, do other side
    aggregate_string += 'When('
    if left_strings_p2:
      aggregate_string += left_strings_p2
    if left_strings_p2 and right_strings_p1:
      aggregate_string += '&'
    if right_strings_p1:
      aggregate_string += right_strings_p1
    aggregate_string += ',then=1))),'

    #p1 wins
    aggregate_string += f'{matchup_name}_wins_{suffix}=Count(Case(When('
    if left_strings_p1:
      aggregate_string += left_strings_p1
    if left_strings_p1 and right_strings_p2:
      aggregate_string += '&'
    if right_strings_p2:
      aggregate_string += right_strings_p2
    aggregate_string += '& Q(player1_victory=True)'
    aggregate_string += ',then=1),'
    #close when, do other side
    aggregate_string += 'When('
    if left_strings_p2:
      aggregate_string += left_strings_p2
    if left_strings_p2 and right_strings_p1:
      aggregate_string += '&'
    if right_strings_p1:
      aggregate_string += right_strings_p1
    aggregate_string += '& Q(player2_victory=True)'
    aggregate_string += ',then=1))),'

    #p1 losses
    aggregate_string += f'{matchup_name}_losses_{suffix}=Count(Case(When('
    if left_strings_p1:
      aggregate_string += left_strings_p1
    if left_strings_p1 and right_strings_p2:
      aggregate_string += '&'
    if right_strings_p2:
      aggregate_string += right_strings_p2
    aggregate_string += '& Q(player1_victory=False)'
    aggregate_string += ',then=1),'
    #close when, do other side
    aggregate_string += 'When('
    if left_strings_p2:
      aggregate_string += left_strings_p2
    if left_strings_p2 and right_strings_p1:
      aggregate_string += '&'
    if right_strings_p1:
      aggregate_string += right_strings_p1
    aggregate_string += '& Q(player2_victory=False)'
    aggregate_string += ',then=1))),'

  #close aggregate
  aggregate_string += ')'
  return aggregate_string


def generate_aggregate_statements_from_basic_openings(data):
  #Have to compare counts against basic strategies to enforce uniqueness
  aggregate_string = f'.aggregate(total=Sum(Case(When((Q(opening1_id__lt={len(Basic_Strategies)}) | Q(opening1_id__gte={len(OPENINGS)-2}))' \
                     f' & (Q(opening2_id__lt={len(Basic_Strategies)}) | Q(opening2_id__gte={len(OPENINGS)-2})), then=F("opening1_victory_count") + F("opening1_loss_count")))),'
  #If user defined openings, then use those, otherwise use the basics
  if len(data['include_opening_ids']) and data['include_opening_ids'][0] != -1:
    strategies = data['include_opening_ids']
  else:
    strategies = range(len(Basic_Strategies + Followups))
  for opening_id in strategies:
      opening_name = OPENINGS[opening_id][0]
      aggregate_string+=f'{opening_name}_total=Sum(Case('
      #need to count each player twice In the case of mirrors
      aggregate_string+=f'When(Q(opening1_id={opening_id}) & Q(opening2_id={opening_id}), then=F("opening1_victory_count") + F("opening1_loss_count") + F("opening2_victory_count") + F("opening2_loss_count")),'
      aggregate_string+=f'When(Q(opening1_id={opening_id}) & (Q(opening2_id__lt={len(Basic_Strategies)}) | Q(opening2_id__gte={len(OPENINGS)-2})), then=F("opening1_victory_count") + F("opening1_loss_count")),'
      aggregate_string+=f'When(Q(opening2_id={opening_id}) & (Q(opening1_id__lt={len(Basic_Strategies)}) | Q(opening1_id__gte={len(OPENINGS)-2})), then=F("opening2_victory_count") + F("opening2_loss_count")))),'

      aggregate_string+=f'{opening_name}_wins=Sum(Case('
      #ignore mirror wins
      aggregate_string+=f'When(Q(opening1_id={opening_id}) & Q(opening2_id={opening_id}), then=0),'
      aggregate_string+=f'When(Q(opening1_id={opening_id}) & (Q(opening2_id__lt={len(Basic_Strategies)}) | Q(opening2_id__gte={len(OPENINGS)-2})), then=F("opening1_victory_count")),'
      aggregate_string+=f'When(Q(opening2_id={opening_id}) & (Q(opening1_id__lt={len(Basic_Strategies)}) | Q(opening1_id__gte={len(OPENINGS)-2})), then=F("opening2_victory_count")))),'

      aggregate_string+=f'{opening_name}_losses=Sum(Case('
      #ignore mirror wins
      aggregate_string+=f'When(Q(opening1_id={opening_id}) & Q(opening2_id={opening_id}), then=0),'
      aggregate_string+=f'When(Q(opening1_id={opening_id}) & (Q(opening2_id__lt={len(Basic_Strategies)}) | Q(opening2_id__gte={len(OPENINGS)-2})), then=F("opening1_loss_count")),'
      aggregate_string+=f'When(Q(opening2_id={opening_id}) & (Q(opening1_id__lt={len(Basic_Strategies)}) | Q(opening1_id__gte={len(OPENINGS)-2})), then=F("opening2_loss_count")))),'


      opening_id += 1
  #close aggregate
  aggregate_string+=')'
  return aggregate_string

def generate_aggregate_statements_from_opening_matchups(data):
  aggregate_string = f'.aggregate(total=Sum(Case(When(Q(opening1_id__lt={len(Basic_Strategies)}) & Q(opening2_id__lt={len(Basic_Strategies)}), then=F("opening1_victory_count") + F("opening1_loss_count")))),'
  #If user defined openings, then use those, otherwise use the basics
  if len(data['include_opening_ids']) and data['include_opening_ids'][0] != -1:
    strategies = data['include_opening_ids']
  else:
    strategies = range(len(Basic_Strategies));
  for i in strategies:
    opening1_name = OPENINGS[i][0]
    for j in strategies[strategies.index(i):]:
      opening2_name = OPENINGS[j][0]
      aggregate_string+=f'{opening1_name}_vs_{opening2_name}_total=Sum(Case('
      aggregate_string+=f'When(Q(opening1_id={i}) & Q(opening2_id={j}), then=F("opening1_victory_count") + F("opening1_loss_count")),'
      if i != j:
        aggregate_string+=f'When(Q(opening2_id={i}) & Q(opening1_id={j}), then=F("opening2_victory_count") + F("opening2_loss_count")))),'
      else:
        aggregate_string+=')),'

      aggregate_string+=f'{opening1_name}_vs_{opening2_name}_wins=Sum(Case('
      aggregate_string+=f'When(Q(opening1_id={i}) & Q(opening2_id={j}), then=F("opening1_victory_count")),'
      if i != j:
        aggregate_string+=f'When(Q(opening2_id={i}) & Q(opening1_id={j}), then=F("opening2_victory_count")))),'
      else:
        aggregate_string+=')),'

  #close aggregate
  aggregate_string+=')'
  return aggregate_string

def generate_filter_statements_from_parameters(data, table_prefix = "", include_opening_ids = True, elo_string = "elo"):
    filter_string = ".filter("

    if 'include_ladder_ids' in data and len(data['include_ladder_ids']) and data['include_ladder_ids'][0] != -1:
        count = 0
        for ladder_id in data['include_ladder_ids']:
            if count >0 and count < len(data['include_ladder_ids']):
                filter_string += ' | '
            filter_string += f'Q({table_prefix}ladder_id={ladder_id})'
            count += 1
        filter_string += ","

    if 'include_patch_ids' in data and len(data['include_patch_ids']) and data['include_patch_ids'][0] != -1:
        count = 0
        for patch_id in data['include_patch_ids']:
            if count >0 and count < len(data['include_patch_ids']):
                filter_string += ' | '
            filter_string += f'Q({table_prefix}patch_number={patch_id})'
            count += 1
        filter_string += ","

    if 'include_map_ids' in data and len(data['include_map_ids']) and data['include_map_ids'][0] != -1:
        count = 0
        for map_id in data['include_map_ids']:
            if count >0 and count < len(data['include_map_ids']):
                filter_string += ' | '
            filter_string += f'Q({table_prefix}map_id={map_id})'
            count += 1
        filter_string += ","
    if 'exclude_civ_mirrors' in data and data['exclude_civ_mirrors'] == 'True':
      filter_string += f'~Q({table_prefix}player1_civilization=F("{table_prefix}player2_civilization")),'

    filter_string += f'{table_prefix}{elo_string}__gte={data["min_elo"]},'
    filter_string += f'{table_prefix}{elo_string}__lte={data["max_elo"]}'
    filter_string += ')'
    return filter_string

def clear_intermediary_tables():
  CivEloWins.objects.all().delete()
  OpeningEloWins.objects.all().delete()
  OpeningEloTechs.objects.all().delete()

def clear_main_tables():
  Matches.objects.all().delete()
  MatchPlayerActions.objects.all().delete()

def update_intermediary_tables():
  build_civ_elo_wins()
  build_opening_elo_wins()
  build_opening_elo_techs()

def build_civ_elo_win_for_match(match, data_dict):
    if (match.player1_civilization == match.player2_civilization) :
      #mirror matches bad
      return
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    #PLAYER 1
    #build dict key for player1
    key = (match.player1_civilization,
           match.map_id,
           match.ladder_id,
           match.patch_number,
           elo)
    if key not in data_dict:
        data_dict[key] = {'victory_count':0, "loss_count":0}
    if match.player1_victory:
        data_dict[key]['victory_count'] += 1
    else:
        data_dict[key]['loss_count'] += 1

    #PLAYER 2
    #build dict key for player2
    key = (match.player2_civilization,
           match.map_id,
           match.ladder_id,
           match.patch_number,
           elo)
    if key not in data_dict:
        data_dict[key] = {'victory_count':0, "loss_count":0}
    if match.player2_victory:
        data_dict[key]['victory_count'] += 1
    else:
            data_dict[key]['loss_count'] += 1

# Run this function to build the civ elo wins table for quicker lookups
def build_civ_elo_wins():
    start = time.time()

    #Use tuples as key to store data in the interim
    # (civ_id,map_id,ladder_id,patch_number,elo) ... ["victory_count", "loss_count"]
    data_dict = {}

    print("Reading from db")
    total = Matches.objects.all().count()
    count = 0
    for match in Matches.objects.all().iterator():
        if count % 5000 == 0:
          print (f'({count} / {total})')
          gc.collect()
        count += 1
        if type(match.average_elo) == str:
          #at least one element has a string elo???? throw it away
          continue
        build_civ_elo_win_for_match(match, data_dict)

    # Now insert records into db
    print("Creating db objects")
    count = 0
    objects = []
    for k,v in data_dict.items():
        if count % 100 == 0:
          print (f'({count} / {len(data_dict)})')
        count += 1
        objects.append(CivEloWins(civilization=k[0],
                                  map_id=k[1],
                                  ladder_id=k[2],
                                  patch_number=k[3],
                                  elo=k[4],
                                  victory_count=v['victory_count'],
                                  loss_count=v['loss_count']))

    # drop all previous records and rebuild
    CivEloWins.objects.all().delete()

    print("Inserting Objects")
    CivEloWins.objects.bulk_create(objects)
    end = time.time()
    print("build_civ_elo_wins - elapsed time", end - start)

def build_opening_elo_win_for_match(match, data_dict):
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    player_openings = []
    #Get players! 1-indexed
    for player in range(1,3):
        valid_openings = []
        opening_index = 0
        #Get openings!
        for opening_info in OPENINGS:
            valid_opening = False
            #opening inclusions
            for inclusion in opening_info[1]:
                valid_inclusion = True
                if not valid_inclusion:
                    break
                #for each bit in bit set
                for i in range(32):
                    #if true bit
                    if inclusion & 2**i:
                        if eval(f'match.player{player}_opening_flag{i}') == False:
                            valid_inclusion = False
                            break
                #Opening is valid if any inclusions are true
                valid_opening |= valid_inclusion
            exclusions = opening_info[2]

            if not len(exclusions):
               exclusions = [OpeningType.Unused.value]
            #opening exclusions
            for exclusion in exclusions:
                if not valid_opening:
                    break
                #for each bit in bit set
                for i in range(32):
                    #if true bit
                    if exclusion & 2**i:
                        if eval(f'match.player{player}_opening_flag{i}') == True:
                            valid_opening = False
                            break
            if valid_opening:
                valid_openings.append(opening_index)
            opening_index += 1
        if not len(valid_openings):
          # Append the unknown opening
          valid_openings.append(len(OPENINGS)-1)
        player_openings.append(valid_openings)
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    #Every player 1 opening played against every player 2 opening
    for p1_opening in player_openings[0]:
        for p2_opening in player_openings[1]:
            #greater opening second to make it simpler on storage
            if p1_opening > p2_opening:
                #swap the 2
                # Hack use a new variable because switching them in place seemed to break things
                opening1 = p2_opening
                opening2 = p1_opening
                p1_win = match.player2_victory
            else:
                opening1 = p1_opening
                opening2 = p2_opening
                p1_win = match.player1_victory
            key = (opening1,
                   opening2,
                   match.map_id,
                   match.ladder_id,
                   match.patch_number,
                   elo)
            if key not in data_dict:
                data_dict[key] = {'opening1_victory_count':0,
                                  "opening1_loss_count":0,
                                  "opening2_victory_count":0,
                                  "opening2_loss_count":0}
            if p1_win:
                data_dict[key]['opening1_victory_count'] += 1
                data_dict[key]['opening2_loss_count'] += 1
            else:
                data_dict[key]['opening1_loss_count'] += 1
                data_dict[key]['opening2_victory_count'] += 1

# Run this function to build the opening elo wins table for quicker lookups
def build_opening_elo_wins():
    start = time.time()

    #Use tuples as key to store data in the interim
    # (opening1_id, opening2_id,map_id,ladder_id,patch_number,elo)
    # ["opening1_victory_count", "opening1_loss_count"]
    data_dict = {}

    print("Reading from db")
    total = Matches.objects.all().count()
    count = 0
    for match in Matches.objects.all().iterator():
        if count % 5000 == 0:
          print (f'({count} / {total})')
          gc.collect()
        count += 1
        if type(match.average_elo) == str:
          #at least one element has a string elo???? throw it away
          continue
        build_opening_elo_win_for_match(match, data_dict)

    # Now insert records into db
    print("Creating db objects")
    count = 0
    objects = []
    def generator():
        for k,v in data_dict.items():
            (yield OpeningEloWins(opening1_id=k[0],
                                  opening2_id=k[1],
                                  map_id=k[2],
                                  ladder_id=k[3],
                                  patch_number=k[4],
                                  elo=k[5],
                                  opening1_victory_count=v['opening1_victory_count'],
                                  opening1_loss_count=v['opening1_loss_count'],
                                  opening2_victory_count=v['opening2_victory_count'],
                                  opening2_loss_count=v['opening2_loss_count']))

    # drop all records and rebuild
    OpeningEloWins.objects.all().delete()
    OpeningEloWins.objects.bulk_create(generator())
    end = time.time()
    print("build_civ_elo_wins - elapsed time", end - start)

def build_opening_elo_techs_for_match_and_action(match, action, data_dict, previous_match_id, previous_match_openings):
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    if match.id == previous_match_id and action.player_id in previous_match_openings:
        player_openings = previous_match_openings
    else:
        player_openings = {}
        #Get players! 1-indexed
        for player in range(1,3):
            opening_index = 0
            player_id = eval(f'match.player{player}_id')
            if player_id not in player_openings:
                player_openings[player_id] = []
            #Get openings!
            for opening_info in OPENINGS:
                valid_opening = False
                #opening inclusions
                for inclusion in opening_info[1]:
                    valid_inclusion = True
                    if not valid_inclusion:
                        break
                    #for each bit in bit set
                    for i in range(32):
                        #if true bit
                        if inclusion & 2**i:
                            if eval(f'match.player{player}_opening_flag{i}') == False:
                                valid_inclusion = False
                                break
                    #Opening is valid if any inclusions are true
                    valid_opening |= valid_inclusion
                exclusions = opening_info[2]

                if not len(exclusions):
                   exclusions = [OpeningType.Unused.value]
                #opening exclusions
                for exclusion in exclusions:
                    if not valid_opening:
                        break
                    #for each bit in bit set
                    for i in range(32):
                        #if true bit
                        if exclusion & 2**i:
                            if eval(f'match.player{player}_opening_flag{i}') == True:
                                valid_opening = False
                                break
                if valid_opening:
                    player_openings[player_id].append(opening_index)
                opening_index += 1
            if not len(player_openings[player_id]):
                # Append the unknown opening
                player_openings[player_id].append(len(OPENINGS)-1)
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    #get techs for match id

    for opening in player_openings[action.player_id]: #1 indexed, remember
        key = (opening,
               action.event_id,
               match.map_id,
               match.ladder_id,
               match.patch_number,
               elo)
        if key not in data_dict:
            data_dict[key] = {'research_count':0,
                              "average_time":0}
        #multiply average by count and add the new time to keep an average without having to store everything
        data_dict[key]['average_time'] =\
            ((data_dict[key]['average_time'] * data_dict[key]['research_count']) + action.time) /\
            (data_dict[key]['research_count']+1)
        data_dict[key]['research_count'] += 1
    return player_openings

def build_opening_elo_techs_for_mpa_match(match, match_player_action, data_dict, previous_match_id, previous_match_openings):
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    if match.id == previous_match_id and match_player_action.player_id in previous_match_openings:
        player_openings = previous_match_openings
    else:
        player_openings = {}
        #Get players! 1-indexed
        for player in range(1,3):
            opening_index = 0
            player_id = eval(f'match.player{player}_id')
            if player_id not in player_openings:
                player_openings[player_id] = []
            #Get openings!
            for opening_info in OPENINGS:
                valid_opening = False
                #opening inclusions
                for inclusion in opening_info[1]:
                    valid_inclusion = True
                    if not valid_inclusion:
                        break
                    #for each bit in bit set
                    for i in range(32):
                        #if true bit
                        if inclusion & 2**i:
                            if eval(f'match.player{player}_opening_flag{i}') == False:
                                valid_inclusion = False
                                break
                    #Opening is valid if any inclusions are true
                    valid_opening |= valid_inclusion
                exclusions = opening_info[2]

                if not len(exclusions):
                   exclusions = [OpeningType.Unused.value]
                #opening exclusions
                for exclusion in exclusions:
                    if not valid_opening:
                        break
                    #for each bit in bit set
                    for i in range(32):
                        #if true bit
                        if exclusion & 2**i:
                            if eval(f'match.player{player}_opening_flag{i}') == True:
                                valid_opening = False
                                break
                if valid_opening:
                    player_openings[player_id].append(opening_index)
                opening_index += 1
            if not len(player_openings[player_id]):
                # Append the unknown opening
                player_openings[player_id].append(len(OPENINGS)-1)
    #round down to nearest delta
    elo = ELO_DELTA * math.floor(match.average_elo/ELO_DELTA)
    #get techs for match id

    for opening in player_openings[match_player_action.player_id]: #1 indexed, remember
        key = (opening,
               match_player_action.event_id,
               match.map_id,
               match.ladder_id,
               match.patch_number,
               elo)
        if key not in data_dict:
            data_dict[key] = {'research_count':0,
                              "average_time":0}
        #multiply average by count and add the new time to keep an average without having to store everything
        data_dict[key]['average_time'] =\
            ((data_dict[key]['average_time'] * data_dict[key]['research_count']) + match_player_action.time) /\
            (data_dict[key]['research_count']+1)
        data_dict[key]['research_count'] += 1
    return player_openings

# Run this function to build the opening elo techs table for quicker lookups
def build_opening_elo_techs():
    start = time.time()

    #Use tuples as key to store data in the interim
    # (opening1_id, opening2_id,map_id,ladder_id,patch_number,elo)
    # ["opening1_victory_count", "opening1_loss_count"]
    data_dict = {}

    print("Reading from db")
    total = MatchPlayerActions.objects.count()
    print(total)
    count = 0
    techs = [tech.id for tech in Techs.objects.all().iterator()]

    #optimization to reduce frequency openings are calculated
    previous_match_id = -2
    previous_match_openings = {}

    for match_player_action in MatchPlayerActions.objects.select_related('match').all().iterator():
        if count % 5000 == 0:
          print (f'({count} / {total}) with {len(data_dict)} elements.')
          gc.collect()
        count += 1
        if match_player_action.event_type != 3 or match_player_action.event_id not in techs:
          continue
        match = match_player_action.match
        if type(match.average_elo) == str:
          #at least one element has a string elo???? throw it away
          continue
        previous_match_openings = build_opening_elo_techs_for_mpa_match(match, match_player_action, data_dict, previous_match_id, previous_match_openings)
        previous_match_id = match.id

    # Now insert records into db
    print("Creating db objects")
    count = 0
    objects = []
    def generator():
        for k,v in data_dict.items():
            (yield OpeningEloTechs(opening_id=k[0],
                                   tech_id=k[1],
                                   map_id=k[2],
                                   ladder_id=k[3],
                                   patch_number=k[4],
                                   elo=k[5],
                                   average_time=v['average_time'],
                                   count=v['research_count']))

    # drop all records and rebuild
    OpeningEloTechs.objects.all().delete()
    OpeningEloTechs.objects.bulk_create(generator(), batch_size=10000)
    end = time.time()
    print("build_opening_elo_techs - elapsed time", end - start)

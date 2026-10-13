import numpy as np
import pandas as pd
pd.options.mode.copy_on_write = True

from google.colab import drive
from google.colab import files
drive.mount('/content/drive',force_remount=True)

from google.colab import auth
auth.authenticate_user()

from googleapiclient.discovery import build
service = build('drive', 'v3')

########################################
########################################

def list_folder_contents(service, folder_id):
    query = f"'{folder_id}' in parents"
    results = service.files().list(q=query, spaces='drive', fields='files(id, name, mimeType)').execute()
    files = results.get('files', [])
    return files

def get_internal_menu_db(parent_folder):
  parent_folder = "/content/drive/MyDrive/Untitled folder/MVP/MVP Chunk #1"
  items_df=pd.read_csv(f'{parent_folder}/new_int_db/items_df.csv')
  vr_df=pd.read_csv(f'{parent_folder}/new_int_db/vr_df.csv')
  ingredients_df=pd.read_csv(f'{parent_folder}/new_int_db/ingredients_df.csv')
  category_df=pd.read_csv(f'{parent_folder}/new_int_db/category_df.csv')

  del ingredients_df["item_id"], items_df['concept']
  items_df = items_df[items_df['ingredients'].notna()]

  return vr_df, category_df, items_df, ingredients_df

def get_available_ingredients(parent_folder, op_id):
  file_path = f'{parent_folder}/Restaurant Matched Ingredients/operator_available_ingredients/op_{op_id}_available_ingredients - ingredients.csv'

  df = pd.read_csv(file_path, header = None)

  return set(
      pd.to_numeric(df[0], errors='coerce'
  ).dropna().astype(int))

def create_helper_tables(
    items_df, ingredients_df, category_df
):

  item_to_category = items_df.set_index('item_id')[
      'category'
  ].str.split(',', expand=True).stack().reset_index(name='category').drop('level_1', axis=1)

  category_to_concept = category_df.set_index([
      'category', 'item_type'
      ])['concept'].str.split(',', expand=True).stack().reset_index(name='concept').drop('level_2', axis=1)

  ing_to_item = items_df.set_index('item_id')[
      'ingredients'
  ].str.split(',', expand=True).stack().reset_index(name='ingredient_id').drop('level_1', axis=1)
  
  return item_to_category, category_to_concept, ing_to_item

def create_dicts(
    items_df, ingredients_df, category_df,
    item_to_category, category_to_concept,
    ing_to_item, vr_df
):
  
  items = items_df[[
      "item_id", "item_name", "item_price", "ingredients"
  ]].set_index("item_id").to_dict(orient='index')

  req_ings = ingredients_df[
      ingredients_df['override']==False
  ]['ingredient_id'].unique()

  for item in items:
    items[item]['ingredients'] = set(map(int, items[item]['ingredients'].split(",")))
    items[item]['required_ingredients'] = {int(ing) for ing in items[item]['ingredients'] if int(ing) in req_ings}

  ingredients = ingredients_df[[
      'ingredient_id', 'ingredient_name', 'price',
        'override', 'disp_description', 'custom_none', 'custom_extra'
  ]].set_index("ingredient_id").to_dict(orient='index')

  concepts = vr_df.set_index("concept").to_dict(orient = "index")

  categories = category_df[[
      'category','item_type'
  ]].set_index("category").to_dict(orient='index')

  for concept in concepts:
    concepts[concept]["categories"] = {}
    this_concept = category_to_concept[
        category_to_concept['concept'] == concept
    ]['category'].unique()
    concepts[concept]["main_items"] = []
    for category in this_concept:
      this_category = item_to_category[
              item_to_category['category'] == category
          ]['item_id'].unique()
      concepts[concept]["categories"][category] = {
          "name" : category,
          "type" : categories[category]['item_type'],
          "items" : list(this_category)
      }
      if categories[category]['item_type'] == "Main Item":
        concepts[concept]["main_items"].extend(list(this_category))

  return items, ingredients, concepts, categories

def unlock_items(items, available_ingredients):

  available_items = set()
  recommended_items = set()

  for item in items:
    if not items[item]['required_ingredients'].issubset(available_ingredients): continue
    if items[item]['ingredients'].issubset(available_ingredients):
      available_items.add(item)
    else:
      recommended_items.add(item)

  available_categories = item_to_category[
      item_to_category['item_id'].isin((available_items|recommended_items))
  ]['category'].unique()

  available_concepts = category_to_concept[
      category_to_concept['category'].isin(available_categories) &
      (category_to_concept['item_type'] == "Main Item")
  ]['concept'].unique()

  return available_items, recommended_items, available_concepts, available_categories

def initialize_mod_groups(ingredients):

  mod_groups = {}
  ing_to_mod = {}

  for ing in ingredients:
    if not (ingredients[ing]['custom_none'] or ingredients[ing]['custom_extra']): continue
    ing_name = ingredients[ing]['ingredient_name']
    ing_price = ingredients[ing]['price']
    mod_name = f"Customize Your {ing_name}"
    mod_groups[mod_name] = {
        "mod_items" : [],
        "is_required" : False,
        "min" : 0,
        "max" : 1
    }
    if ingredients[ing]['custom_none']:
      mod_groups[mod_name]["mod_items"].append((f"no {ing_name}", ing_price))
    if ingredients[ing]['custom_extra']:
      mod_groups[mod_name]["mod_items"].append((f"extra {ing_name}", ing_price))
    ing_to_mod[ing] = mod_name
  mod_groups

  return mod_groups, ing_to_mod

def create_vrs(
    concepts, available_concepts, available_items,
    recommended_items, item_to_category,
    category_to_concept, ing_to_item, mod_groups
):

  MIN = 6
  vrs, statuses = {}, {}
  all_addons, item_to_addon_mod = {}, {}
  non_main_items = {
      'Side Item' : [],
      'Dessert Item' : [],
      'Beverage Item' : []
  }

  for concept in concepts:

    if concept not in available_concepts: continue

    # Gather all avialable and recommended main items of this concept
    main_items = [
        item for item in concepts[concept]['main_items'] if item in (available_items | recommended_items)
    ]
    main_item_len = len(main_items)

    # check if you need to consider item size to get true main item count
    if concepts[concept]['size_consideration'] == True:
      categories = item_to_category[
          item_to_category['item_id'].isin(main_items)
      ]['category'].unique()
      main_item_len /= (len(categories))

    # check if the count of main items meets minimum to create a vr for it
    if len(main_items) < MIN:
      continue

    else:
      availability = "Available"
      if len(set(main_items) - recommended_items) < MIN:
        availability = "Recommended"

      statuses[concept] = availability

      vrs[concept] = {}

      # For creating addon modifiers for each item,
      # starting with including all ingredients appropriate to be an addon
      available_main_items = [
        item for item in concepts[concept]["main_items"]
        if item in (available_items | recommended_items)
      ]

      main_ings = ing_to_item[
          ing_to_item['item_id'].isin(available_main_items)
      ]['ingredient_id'].unique()

      all_addons[concept] = []

      for ing in main_ings:
        if not ingredients[int(ing)]['custom_extra']: continue
        all_addons[concept].append(int(ing))

      # Begin creating categories for each concept
      for category in concepts[concept]['categories']:

        if category not in available_categories: continue

        vrs[concept][category] = {}

        for item in concepts[concept]['categories'][category]['items']:
          if item not in (available_items | recommended_items): continue
          item_name = items[item]['item_name']
          item_price = items[item]['item_price']
          item_ingredients = items[item]['ingredients']
          kitchen_description = ", ".join(
            ingredients[ing]['ingredient_name'] for ing in item_ingredients
            if ingredients[ing]['disp_description']
          )

          is_recommended = False
          if item in recommended_items: is_recommended = True

          # Input prices of non main items and create modifiers for them
          item_type = concepts[concept]['categories'][category]['type']
          if item_type != "Main Item":
            item_price = 5# float(input(f"input the price of {item_name}"))
            non_main_items[f'{item_type}'].append((item_name, item_price))
          
          vrs[concept][category][item] = {
              "item_name" : item_name,
              "item_price" : item_price,
              "ingredients" : item_ingredients,
              "kitchen_description" : kitchen_description,
              "is_recommended" : is_recommended
          }

          # Create Addon modifiers for this item
          addon_ings = set(all_addons[concept]) - set(item_ingredients)

          if addon_ings:
            mod_items = []
            mod_name = f"Addons for {item_name}"
            for ing in addon_ings:
              mod_items.append((f"Add {ingredients[ing]['ingredient_name']}", ingredients[ing]['price']))
            mod_groups[mod_name] = {
                  "mod_items" : mod_items,
                  "is_required" : False,
                  "min" : 0,
                  "max" : len(addon_ings)
              }
            item_to_addon_mod[item] = mod_name
          else: item_to_addon_mod[item] = None

  default_prices = get_default_prices(vrs, concepts)

  vrs = apply_default_prices(default_prices, vrs)

  mod_groups = create_non_main_dict(non_main_items, mod_groups)

  return vrs, statuses, item_to_addon_mod, non_main_items, mod_groups

def get_default_prices(vrs, concepts):

  default_prices = {}

  for concept in vrs:
    for category in vrs[concept]:
      if concepts[concept]['categories'][category]['type'] != "Main Item":
        continue
      for item in vrs[concept][category]:
        if items[item]['item_price'] == 0:
          default_prices[(concept, category)] = float(input(f"Input the price of {items[item]['item_name']}: "))
          break

  return default_prices

def apply_default_prices(default_prices, vrs):

  #PERCENTAGE_INCREASE = 0.25 # Default % increase
  PERCENTAGE_INCREASE = float(input(
      """Input the percentage increase for the default price of this operator\n
      Input percentage in this format --> 0.##\n example: 0.25, which accounts for 25%: """
  ))

  # Input default pricing if each category of type "Main Item"

  for cncpt_ctgry in default_prices:
    for item in vrs[cncpt_ctgry[0]][cncpt_ctgry[1]]:
      updated_price = default_prices[cncpt_ctgry] + items[item]['item_price']
      vrs[cncpt_ctgry[0]][cncpt_ctgry[1]][item]['item_price'] = updated_price
  
  return vrs

def create_non_main_dict(non_main_items, mod_groups):

  for item_type in non_main_items:
    if not non_main_items[item_type]: continue
    mod_groups[f"Add {item_type}s"] = {
        "mod_items" : non_main_items[item_type],
        "is_required" : False,
        "min" : 0,
        "max" : len(non_main_items[item_type])
    }

  return mod_groups

# Result Dicts to DataFrames

def create_mod_ids(mod_groups):

  mod_group_ids, id = {}, 1
  for mod in mod_groups:
    mod_group_ids[mod] = id
    id += 1

  return mod_group_ids

def convert_vrs_to_df(
    vrs, mod_group_ids, item_mods,
    non_main_items, ing_to_mod,
    item_to_addon_mod
):

  concepts_mods = {}

  vrs_result_df = {
            "concept": [],
            "category": [],
            "item_name": [],
            "item_price": [],
            "kitchen_description": [],
            "item_mod_ids": [],
            "is_recommended": []
        }

  for concept in vrs:
    concepts_mods[concept] = []

    for category in vrs[concept]:
      for item in vrs[concept][category]:

        item_mods = [
            ing_to_mod[ing] for ing in ing_to_mod
            if ing in items[item]['ingredients']
        ]
        if item_to_addon_mod[item]: item_mods.append(item_to_addon_mod[item])

        # If this type is NON MAIN ITEM then remove customization modifiers

        item_type = concepts[concept]['categories'][category]['type']
        if item_type != "Main Item": item_mods = []

        # Add non main item modifier groups that is NOT the same item type of this item

        for non_main_item in non_main_items:

          if item_type == non_main_item: continue
          if not non_main_items[non_main_item]: continue
          item_mods.append(f"Add {non_main_item}s")

        concepts_mods[concept].extend(
            [mod_group_ids[mod_group] for mod_group in item_mods
            if mod_group_ids[mod_group] not in concepts_mods[concept]]
        )

        item_mod_ids = "-".join(
            str(mod_group_ids[mod_group]) for mod_group in item_mods
        )

        vrs_result_df['concept'].append(concept)
        vrs_result_df['category'].append(category)
        vrs_result_df['item_name'].append(vrs[concept][category][item]['item_name'])
        vrs_result_df['item_price'].append(vrs[concept][category][item]['item_price'])
        vrs_result_df['kitchen_description'].append(vrs[concept][category][item]['kitchen_description'])
        vrs_result_df['item_mod_ids'].append(item_mod_ids)
        vrs_result_df['is_recommended'].append(vrs[concept][category][item]['is_recommended'])

  return pd.DataFrame(vrs_result_df), concepts_mods

def convert_mod_groups_to_df(mod_groups, mod_group_ids):

  mod_groups_df = {
      'modifer_group_id': [],
      'modifier_group_name': [],
      'modifier_name': [],
      'min': [],
      'max': [],
      'is_required': [],
      'modifier_price': []
  }

  for mod_group in mod_groups:
    is_required = mod_groups[mod_group]['is_required']
    min = mod_groups[mod_group]['min']
    max = mod_groups[mod_group]['max']
    for mod_item in mod_groups[mod_group]['mod_items']:
      mod_groups_df['modifer_group_id'].append(mod_group_ids[mod_group])
      mod_groups_df['modifier_group_name'].append(mod_group)
      mod_groups_df['modifier_name'].append(mod_item[0])
      mod_groups_df['min'].append(min)
      mod_groups_df['max'].append(max)
      mod_groups_df['is_required'].append(is_required)
      mod_groups_df['modifier_price'].append(mod_item[1])
      row = {
          'modifer_group_id': mod_group_ids[mod_group],
          'modifier_group_name': mod_group,
          'modifier_name': mod_item[0],
          'min': min,
          'max': max,
          'is_required': is_required,
          'modifier_price': mod_item[1]
      }

  return pd.DataFrame(mod_groups_df)

# Main Functions

def vr_gen(parent_folder, op_id):

  vr_df, category_df, items_df, ingredients_df = get_internal_menu_db(parent_folder)

  available_ingredients = get_available_ingredients(parent_folder, op_id)

  item_to_category, category_to_concept, ing_to_item = create_helper_tables(
      items_df, ingredients_df, category_df
  )

  items, ingredients, concepts, categories = create_dicts(
      items_df, ingredients_df, category_df,
      item_to_category, category_to_concept,
      ing_to_item, vr_df
  )

  available_items, recommended_items, available_concepts, available_categories = unlock_items(
      items, available_ingredients
  )

  mod_groups, ing_to_mod = initialize_mod_groups(ingredients)

  vrs, statuses, item_to_addon_mod, non_main_items, mod_groups = create_vrs(
      concepts, available_concepts, available_items,
      recommended_items, item_to_category,
      category_to_concept, ing_to_item, mod_groups
  )

  return [vrs, mod_groups, statuses, concepts, items, ingredients, item_to_category, category_to_concept, ing_to_item]


def convert_to_df(
    mod_groups, vrs
):
  mod_group_ids = create_mod_ids(mod_groups)

  vrs_result_df, concepts_mods = convert_vrs_to_df(
      vrs, mod_group_ids, item_mods,
    non_main_items, ing_to_mod,
    item_to_addon_mod
  )

  mod_groups_df = convert_mod_groups_to_df(mod_groups, mod_group_ids)

  return vrs, mod_groups, vrs_result_df, mod_groups_df, statuses, concepts_mods

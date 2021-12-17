# Copyright INRIM (https://www.inrim.eu)
# See LICENSE file for full licensing details.
import sys
from app import config

from datetime import datetime, timedelta
import bson
import logging
import pymongo
from pymongo import ReadPreference
from .mongodb import get_database, db
from .bson_types import *
from .base_model import default_list_metadata_fields, default_list_metadata_fields_update
from fastapi.encoders import jsonable_encoder

from .base_model import *
import ujson

logger = logging.getLogger(__name__)


# TODO handle update schema to test
# db.foo.updateMany( {}, <update> )
# db.foo.updateMany({"created": false}, {"$set":{"created": true}});
# motor
# await coll.update_many({'i': {'$gt': 100}},
#                        {'$set': {'key': 'value'}})

## TODO helper function
def _data_helper(d):
    if isinstance(d, bson.objectid.ObjectId) or isinstance(d, datetime):
        return str(d)

    if isinstance(d, list):  # For those db functions which return list
        return [_data_helper(x) for x in d]

    if isinstance(d, dict):
        for k, v in d.items():
            d.update({k: _data_helper(v)})

    # return anything else, like a string or number
    return d


def data_helper(d):
    d = _data_helper(d)
    # d = jsonable_encoder(d)
    return d


def get_data_list(
        list_data, fields=[], merge_field="", row_action="",
        additional_key=[], remove_keys=[]):
    new_list = []
    for i in list_data:
        if not isinstance(i, dict):
            data = ujson.loads(i.json())
        else:
            data = data_helper(i)
        if row_action:
            data['row_action'] = f"{row_action}/{data['rec_name']}"
        if additional_key:
            data[additional_key[0]] = data[additional_key[1]]
        if remove_keys:
            for k, v in data.items():
                if isinstance(v, dict):
                    for k1 in remove_keys:
                        if k1 in v:
                            v.pop(k1)
            for k in remove_keys:
                if k in data:
                    data.pop(k)
        new_list.append(data_helper_list(
            data, fields=fields, merge_field=merge_field))
    return new_list


def data_helper_list(d, fields=[], merge_field=""):
    dres = {}
    data = d
    if fields:
        dres = {}
        if merge_field:
            for k in fields:
                if not k == merge_field:
                    data[merge_field][k] = data.get(k)
            res = data[merge_field].copy()
            return res
        else:
            for k in fields:
                dres[k] = data.get(k)
            return dres
    else:
        return data


async def set_unique(model: Type[ModelType], field_name):
    component_coll = db.engine.get_collection(model.schema().get('title', "").lower())
    await component_coll.create_index([(field_name, 1)], unique=True)


async def prepare_collenctions():
    await set_unique(Component, "rec_name")
    await set_unique(Component, "title")


async def get_collections_names(query={}):
    if not query:
        query = {"name": {"$regex": r"^(?!system\.)"}}
    collection_names = await db.engine.list_collection_names(filter=query)
    return collection_names


## TODO handle records
async def search_distinct(model: Type[ModelType], distinct="rec_name", clausole={}):
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    values = await coll.distinct(distinct, clausole)
    return values


async def search_by_filter(model: Type[ModelType], domain: dict, sort: list = [], limit=0, skip=0):
    logger.debug(
        f"search_by_filter: schema:{model}, domain:{domain}, sort:{sort}, limit:{limit}, skip:{skip}")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    if limit > 0:
        datas = await coll.find(domain).sort(sort).skip(skip).limit(limit).to_list(None)
    elif sort:
        datas = await coll.find(domain).sort(sort).to_list(None)
    else:
        datas = await coll.find(domain).to_list(None)
    return datas


async def find_one(model: Type[ModelType], domain: dict):
    logger.debug(f"find_one: schema:{model}, domain:{domain}")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    obj = await coll.find_one(domain)
    if obj:
        logger.debug(obj.get('_id'))
        return model(**obj)
    else:
        logger.debug("not found")
        return obj


async def aggregate(model: Type[ModelType], pipeline: dict, sort: list = [], limit=0, skip=0):
    logger.debug(
        f"aggregate: schema:{model}, pipeline:{type(domain)}, sort:{sort}, limit:{limit}, skip:{skip}")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    if limit > 0:
        datas = await coll.aggregate(pipeline).sort(sort).skip(skip).limit(limit).to_list(None)
    else:
        datas = await coll.aggregate(pipeline).sort(sort).to_list(None)

    return datas


async def count_by_filter(model: Type[ModelType], domain: dict) -> int:
    logger.debug(f"count_by_filter:{model.schema()['title'].lower()}  - {domain}")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    val = await coll.count_documents(domain)
    if not val:
        val = 0
    logger.debug(f"found: {val} items")
    return int(val)


async def search_all(model: Type[ModelType], sort: list = [], limit=0, skip=0) -> List[ModelType]:
    datas = await search_by_filter(model, {}, sort=sort)
    return datas


async def search_all_distinct(
        model: Type[ModelType], distinct="", query={}, compute_label="", sort: list = []) -> List[ModelType]:
    logger.debug("search_all_distinct")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    if not query:
        query = {"deleted": 0}
    label = {"$first": f"$title"}
    label_lst = compute_label.split(",")
    if compute_label:
        if len(label_lst) > 0:
            block = []
            for item in label_lst:
                if len(block) > 0:
                    block.append(f" - ")
                block.append(f"${item}")
            label = {"$first": {"$concat": block}}

        else:
            label = {"$first": f"${label_lst[0]}"}

    pipeline = [
        {"$match": query},
        {
            "$group":
                {
                    "_id": "$_id",
                    f"{distinct}": {"$first": f"${distinct}"},
                    "title": label,
                    "type": {"$first": f"$type"}
                }
        },
        {'$sort': {'title': 1}}
    ]
    res = await coll.aggregate(pipeline).to_list(length=None)
    return res


async def search_count_field_value_freq(
        model: Type[ModelType], field="", field_query={}, min_occurence=2, add_fields="", sort=-1) -> List[ModelType]:
    logger.debug("search_all_distinct")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    group = {
        "_id": f'${field}',
        "count": {"$sum": 1}
    }

    if add_fields:
        label_lst = add_fields.split(",")
        for item in label_lst:
            group.update({f"$item": {"$first": item}})

    query = {
        "$and": [{"deleted": 0}, field_query]
    }
    pipeline = [
        {"$match": query},
        {
            "$group": group
        },
        {
            "$match": {
                "count": {"$gte": min_occurence}
            }
        },
        {'$sort': {'count': sort}}
    ]
    res = await coll.aggregate(pipeline).to_list(length=None)
    return res


async def search_by_type(schema: Type[ModelType], model_type: str, sort: Optional[Any] = None) -> List[ModelType]:
    query = {"$and": [{"type": model_type}, {"deleted": 0}]}
    if not sort:
        sort = [("list_order", pymongo.ASCENDING), ("rec_name", pymongo.ASCENDING)]
    datas = await search_by_filter(schema, query, sort=sort)
    return datas


# Retrieve a form with a matching ID
async def search_by_id(model: Type[ModelType], rec_id: str) -> Type[ModelType]:
    query = {"_id": bson.ObjectId(rec_id)}
    data = await find_one(model, query)
    if data:
        return data
    else:
        return False


async def search_by_name(model: Type[ModelType], rec_name: str):
    query = {"rec_name": rec_name}
    data = await find_one(model, query)
    if data:
        return data
    else:
        return False


async def search_by_uid(model: Type[ModelType], rec_name: str):
    query = {"uid": rec_name}
    data = await find_one(model, query)
    if data:
        return data
    else:
        return False


async def search_user_by_token(model: Type[ModelType], token: str):
    query = {"token": token}
    data = await find_one(model, query)
    # data = await engine.find_one(schema, schema.token == token)
    if data:
        return data
    else:
        return False


async def save_record(record, remove_meta=True):
    logger.debug(f" model {type(record)}")
    model = type(record).__name__.lower()
    coll = db.engine.get_collection(model)
    candidate = ujson.loads(record.json())
    original = False
    filter_key = {}
    if candidate.get("rec_name", False):
        original = await search_by_name(type(record), candidate['rec_name'])
        filter_key = {"rec_name": candidate["rec_name"]}

    if not original:
        original = await search_by_id(type(record), candidate['id'])
        filter_key = {"_id": bson.ObjectId(candidate["id"])}

    if original:
        original_dict = ujson.loads(original.json())
        if remove_meta:
            [original_dict.pop(key) for key in default_list_metadata_fields_update if key in original_dict]
        diff = {k: v for k, v in candidate.items() if k in original_dict and not original_dict[k] == v}
        to_save = diff
        if to_save:
            result_save = await coll.update_one(filter_key, {"$set": to_save})
            result = False
            if result_save:
                logger.debug(f" executed to {result_save.modified_count} records")
                result = await find_one(type(record), filter_key)
            return result
        else:
            return original
    else:
        result_save = await coll.insert_one(candidate)
        result = False
        if result_save:
            logger.debug(f" insert {result_save.inserted_id} record")
            filter_key = {"_id": result_save.inserted_id}
            result = await find_one(type(record), filter_key)
        return result


async def save_all(list_data, remove_meta=True):
    updated_list = []
    for rec in list_data:
        new_rec = await save_record(rec, remove_meta=remove_meta)
        updated_list.append(new_rec)
    return updated_list


## TODO delete handler

async def delete_record(record):
    logger.info(f" model {type(record)}")
    model = type(record).__name__.lower()
    coll = db.engine.get_collection(model)
    return await coll.delete_one({"_id": record.id})


async def set_to_delete_records(model: Type[ModelType], query={}):
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    records = await coll.find(query).to_list(None)
    settings = config.SettingsApp()
    for rec in records:
        rec['id'] = rec['_id']
        record = schema(**rec)
        delete_at_datetime = datetime.now() + timedelta(days=settings.delete_record_after_days)
        record.deleted = delete_at_datetime.timestamp()
        await engine.save(record)
    return True


async def delete_records(model: Type[ModelType], query={}):
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    records = await coll.find(query).to_list(None)
    for rec in records:
        # record = jsonable_encoder(rec)
        # logger.info(rec)
        await coll.delete_one({"_id": rec['_id']})
    return True


async def set_to_delete_record(schema: Type[ModelType], rec):
    settings = config.SettingsApp()
    delete_at_datetime = datetime.now() + timedelta(days=settings.delete_record_after_days)
    rec.deleted = delete_at_datetime.timestamp()
    return await save_record(rec, remove_meta=False)


async def retrieve_all_to_delete(model: Type[ModelType]):
    curr_timestamp = datetime.now().timestamp()
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    res = await coll.find({"deleted": {"$lte": curr_timestamp}}).to_list(None)
    return res


async def erese_all_to_delete_record(model: Type[ModelType]):
    res = await retrieve_all_to_delete(model)
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    for rec in res:
        await coll.delete_one({"_id": rec["id"]})
    return f"removed {len(res)} records"


async def clean_session(date_expire):
    coll = db.engine.get_collection("session")
    res_to_expire = await coll.find({"expire_datetime": {"$lt": date_expire}}).to_list(None)
    for item in res_to_expire:
        await coll.delete_one({"_id": item["_id"]})
    res = await coll.find({"active": False}).to_list(None)
    for rec in res:
        await coll.delete_one({"_id": rec["_id"]})
    return f"removed {len(res) + len(res_to_expire)} records"


## TODO handle archiviations

async def retrieve_all_archivied(model: Type[ModelType]):
    res = await search_by_filter(model, {"active": True})
    return res


async def set_active(schema: Type[ModelType], rec_id: str):
    rec = await search_by_id(schema, rec_id)
    rec.active = True
    return await save_record(rec)


async def set_archivied(schema: Type[ModelType], rec_id: str):
    rec = await search_by_id(schema, rec_id)
    rec.active = False
    return await save_record(rec)


# TODO handle collections index

async def get_collection_index_fields(schema):
    logger.info("get_collection_index_fields")
    coll = db.engine.get_collection(model.schema().get('title', "").lower())
    fields = []
    async for index in coll.list_indexes():
        item = data_helper(index)
        fields.append(item)
    return fields

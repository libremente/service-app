# Copyright INRIM (https://www.inrim.eu)
# See LICENSE file for full licensing details.
import copy
import sys
import os
from os import listdir
from os.path import isfile, join
import ujson
from ozon.settings import get_settings
from .database.mongo_core import *
from .database.cache.cache import get_cache
from collections import OrderedDict
from pathlib import Path
from fastapi import Request
from .ServiceSecurity import ServiceSecurity
from .ServiceAction import ServiceAction
from .ServiceActionTask import ActionTask
from .ServiceMenuManager import ServiceMenuManager
from .QueryEngine import QueryEngine
from .ModelData import ModelData
from .BaseClass import BaseClass, PluginBase
from .ServiceAuth import ServiceAuth
from pydantic import ValidationError
import logging
import pymongo
import requests
import httpx
import uuid
import traceback

logger = logging.getLogger(__name__)


# basedir = os.path.abspath(os.path.dirname(__file__))
# UPLOAD_FOLDER = f'/uploads'
# Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)

class ServiceMain(PluginBase):
    plugins = []

    def __init_subclass__(cls, **kwargs):
        cls.plugins.append(cls())


class ServiceBase(ServiceMain):

    @classmethod
    def create(
            cls, request
    ):
        self = ServiceBase()
        self.init(request)
        return self

    def init(self, request):
        self.request = request
        self.session = request.scope['ozon'].session
        self.pwd_context = request.scope['ozon'].pwd_context
        self.action_service = None
        self.app_code = request.headers.get('app_code', "admin")
        self.mdata = ModelData.new(session=self.session, pwd_context=self.pwd_context, app_code=self.app_code)
        self.menu_manager = ServiceMenuManager.new(
            session=self.session, pwd_context=self.pwd_context, app_code=self.app_code)
        self.acl = ServiceSecurity.new(
            session=self.session, pwd_context=self.pwd_context, app_code=self.app_code)
        self.qe = QueryEngine.new(
            session=self.session, app_code=self.app_code)
        self.asc = 1
        self.desc = -1

    async def make_settings(self):
        # logger.info("load--")
        self.app_settings = await self.mdata.get_app_settings(app_code=self.app_code)

    async def get_param(self, name: str) -> Any:
        return await get_param(name)

    async def service_handle_action(
            self, action_name: str, data: dict = {}, rec_name: str = "",
            parent="", iframe="", execute=False, container_act=""):
        logger.info(f"service_handle_action -> name:{action_name}, rec_name:{rec_name}, "
                    f"execute:{execute}, data:{data.keys()}, container_act: {container_act}")
        await self.make_settings()
        if not data:
            data = {
                "limit": 0,
                "skip": 0,
                "sort": "",
                "query": {}
            }

        self.action_service = ServiceAction.new(
            session=self.session, service_main=self, action_name=action_name,
            rec_name=rec_name, parent=parent, iframe=iframe, execute=execute,
            pwd_context=self.pwd_context, container_act=container_act
        )
        await self.action_service.make_settings()
        act_data = await self.action_service.compute_action(data=data)
        return {
            "settings": {
                "module_name": get_settings().module_name,
                "version": get_settings().version,
                "logo_img_url": get_settings().logo_img_url
            },
            **act_data.copy()
        }

    async def service_get_layout(self, name):
        await self.make_settings()
        logger.debug("service_get_default_layout")
        if not name:
            name = self.session.app.get("layout")
        else:
            session.app['layout'] = name

        layout = await search_by_name(Component, rec_name=name)

        return {
            "mode": "system",
            "settings": {
                "module_name": self.app_settings.rec_name,
                "version": self.app_settings.version,
                "logo_img_url": self.app_settings.logo_img_url
            },
            "menu": await self.menu_manager.make_main_menu(),
            "schema": layout
        }

    async def service_get_dashboard(self, parent=""):
        logger.debug(f"service_get_dashboard {parent}")
        await self.make_settings()
        return {
            "model": "action",
            "content": {
                "mode": "cards",
                "cards": await self.menu_manager.make_dashboard_menu(parent=parent)
            }
        }

    async def service_get_schema(self, model_name):
        logger.debug(f"service_get_schema by name {model_name}")
        await self.make_settings()
        # TODO add check rules for model
        schema = await self.mdata.component_by_name(model_name)
        return schema or {}

    async def service_get_schema_model(self, model_name):
        logger.debug(f"service_get_schema by name {model_name}")
        await self.make_settings()
        schema_model = await self.mdata.gen_model(model_name)
        if not schema_model.schema():
            return {}
        schema = schema_model.schema()
        model_fields_names = [k for k, v in schema['properties'].items()]
        fields = [item for item in model_fields_names if item not in default_list_metadata_fields]
        if "data_value" in fields:
            fields.remove("data_value")
        res = {
            "mode": "system",
            "schema": schema,
            "fields": fields,
            "metadata": default_list_metadata_fields
        }
        return res

    async def service_reorder_record(self, data):
        logger.debug(f"service_reorder_record by name {data}")
        # TODO add check rules for model
        await self.make_settings()
        model_data = await self.mdata.gen_model(data['model_name'])
        list_to_save = []
        for record_data in data['columns']:
            record = await self.mdata.by_name(model_data, record_data['key'])
            record.list_order = record_data['value']
            if not data['model_name'] == "component":
                record.data_value['list_order'] = record_data['value']
            list_to_save.append(record)
        await self.mdata.save_all(list_to_save, remove_meta=False)
        return {"status": "ok"}

    async def service_get_schemas_by_type(self, schema_type="form", query={}, fields=[], additional_key=[]):
        logger.info(
            f"service_get_schemas_by_type  schema_type:{schema_type}, query:{query}, "
            f"fields:{fields},additional_key:{additional_key}"
        )
        await self.make_settings()
        # TODO add check rules
        query = {
            "$and": [
                {"deleted": 0},
                {"type": {"$eq": schema_type}}
            ]
        }
        data = await self.mdata.all_distinct(
            Component, "rec_name", query=query, additional_key=additional_key)
        return {
            "content": {
                "mode": "list",
                "data": data or [],
            }
        }

    async def service_get_schemas_by_parent_and_type(
            self, parent_model, schema_type="form", fields=[], additional_key=[]):
        logger.info(f"service_get_schema by name {parent_model}")
        # TODO add check rules parent_model
        query = {
            "$and": [
                {"parent": {"$eq": parent_model}},
                {"deleted": 0}
            ]
        }
        await self.make_settings()
        data = await self.mdata.get_list_base(
            Component, fields=fields, query=query, model_type=schema_type, additional_key=additional_key)
        return {
            "content": {
                "mode": "list",
                "data": data or [],
            }
        }

    async def service_get_data_for_model(
            self, model_name, query={}, fields=[], additional_key=[]):
        logger.info(f"get_data_model {model_name}")
        await self.make_settings()
        # TODO add check read rules model_name
        data_model = await self.mdata.gen_model(model_name)
        if model_name == "component":
            # schema = data_model
            sort = []
        else:
            schema = await self.mdata.component_by_name(model_name)
            sort = self.mdata.eval_sort_str(schema.properties.get("sort", ''))
        query = await self.qe.default_query(
            data_model, query)
        data = await self.mdata.get_list_base(
            data_model, fields=fields, query=query, sort=sort)
        return {
            "content": {
                "mode": "list",
                "data": data or []
            }
        }

    async def service_get_data_view(
            self, model_name, query={}, fields=[], additional_key=[]):
        logger.info(f"service_get_data_view {model_name}")
        await self.make_settings()
        # TODO add check read rules model_name
        data = await self.mdata.search_view(
            model_name, query=query)
        return {
            "content": {
                "mode": "list",
                "data": data or []
            }
        }

    async def service_get_record(self, model_name, rec_name):
        logger.info(f"service_get_record by name model_name:{model_name}, rec_name:{rec_name}")

        # TODO add check read rules for model
        await self.make_settings()
        schema = await self.mdata.component_by_name(model_name)
        data_model = await self.mdata.gen_model(model_name)
        data = await self.mdata.by_name(
            data_model, record_name=rec_name)
        logger.info(data)
        if not data:
            data = data_model(**{})
        can_edit = await self.acl.can_update(schema, data)
        return {
            "content": {
                "editable": can_edit,
                "mode": "form",
                "model": model_name,
                "schema": schema or {},
                "data": data or {},
            }
        }

    async def service_component_distinct_model(self):
        logger.info(f"service_component_distinct_model")
        # TODO add check read rules for model
        await self.make_settings()
        query = {
            "$and": [
                # {"type": {"$eq": "form"}},
                {"deleted": 0},
                {"data_model": {"$eq": ""}}
            ]
        }
        data = await self.mdata.all_distinct(
            Component, "rec_name", query=query)
        return {
            "content": {
                "mode": "list",
                "data": data or [],
            }
        }

    async def service_distinct_rec_name_by_model(
            self, model_name="component", domain={}, props={}):
        logger.info(
            f"service_component_distinct_model model_name:{model_name}, domain:{domain}, props:{props}")
        # TODO add check read rules for model
        await self.make_settings()
        data = []
        if model_name:
            model_data = await self.mdata.gen_model(model_name)
            data = await self.mdata.all_distinct(
                model_data, "rec_name", query=domain, compute_label=props.get("compute_label", ""))
            if model_name == "component":
                data.append(
                    {
                        "_id": "component",
                        "rec_name": "component",
                        "title": "Component",
                        "type": ""
                    },
                )
        return {
            "content": {
                "mode": "list",
                "data": data or [],
            }
        }

    async def service_freq_for_field_model(
            self, model_name="", field="", field_query={}, min_occurence=2, add_fields="", sort=-1):
        logger.info(
            f"gen freq model_name:{model_name}, field:{field}, field_query:{field_query}, min_occurence: {min_occurence}")

        data = []
        await self.make_settings()
        if model_name and field:
            model_data = await self.mdata.gen_model(model_name)
            data = await self.mdata.freq_for_all_by_field_value(
                model_data, field=field, field_query=field_query, min_occurence=min_occurence, add_fields=add_fields,
                sort=sort)
        return {
            "content": {
                "mode": "list",
                "data": data or [],
            }
        }

    async def get_remote_data_select(self, url, path_value, header_key, header_value_key):
        await self.make_settings()
        if path_value:
            url = f"{url}/{path_value}"
        cache = await get_cache()
        editing = self.session.app.get("builder")
        memcache = await cache.get(self.app_code, f"get_remote_data_select:{url}")
        if memcache and not editing:
            values = memcache.get("content", {}).get("data", [])
            logger.info(values)
            if len(values) > 0:
                logger.info(f"cache usage")
                return memcache
        rec_cfg = await self.get_param(header_value_key)
        headers = {}
        if isinstance(rec_cfg, dict):
            remote_data = await self.get_remote_data(headers, header_key, rec_cfg.get("key"), url)
        else:
            remote_data = await self.get_remote_data(headers, header_key, rec_cfg, url)
        data = remote_data if isinstance(remote_data, list) else []
        res = {
            "content": {
                "mode": "list",
                "data": data
            }
        }
        if data and len(data) > 0:
            await cache.set(self.app_code, f"get_remote_data_select:{url}", res, expire=1800)
        return res

    async def get_remote_data(self, headers={}, header_key="", header_value="", url=""):
        logger.info(f"server get_remote_data --> {url}, header_key:{header_key}, header_value:{header_value} ")
        await self.make_settings()
        if header_key and header_value:
            headers.update({
                "Content-Type": "application/json",
                header_key: header_value
            })
        else:
            headers.update({
                "Content-Type": "application/json",
            })

        async with httpx.AsyncClient(timeout=None) as client:
            res = await client.get(
                url=url, headers=headers
            )
        if res.status_code == 200:
            logger.info(f"server get_remote_data --> {url} SUCCESS ")
            datar = res.json()
            data = copy.deepcopy(datar)
            if isinstance(datar, dict) and datar.get("result"):
                if isinstance(datar.get("result"), dict) and datar.get("result").get("select_list"):
                    data = datar.get("result", {}).get("select_list", [])
                if isinstance(datar.get("result"), list):
                    data = datar.get("result", [])
        else:
            logger.info(f"server get_remote_data --> {url} Error {res.status_code} ")
            data = {}

        # client.close()
        return data

    async def export_data(self, model_name, datas, parent_name=""):
        logger.info(f" model:{model_name}, query:{datas}, parent_name:{parent_name}")
        await self.make_settings()
        # data_mode = json | value
        data_mode = datas.get('data_mode', 'json')

        data_model = await self.mdata.gen_model(model_name)
        query = await self.qe.default_query(data_model, datas['query'])
        if model_name == 'component':
            model = await self.mdata.gen_model(model_name)
            list_schema = await self.mdata.search_base(model, query=query)
            if list_schema:
                schema_dict = {}
                if isinstance(list_schema[0], dict):
                    schema_dict = list_schema[0].copy()
                elif isinstance(list_schema, BasicModel):
                    schema_dict = list_schema.get_dict()
                schema = model(**schema_dict)
        else:
            schema = await self.mdata.component_by_name(model_name)

        sort = self.mdata.eval_sort_str(schema.properties.get("sort", ''))

        if not data_mode == 'json':
            data = await self.mdata.search_export(
                data_model, fields=['data_value'], merge_field="data_value", query=query, parent=parent_name,
                remove_keys=["_id", "id"], sort=sort
            )
        else:
            if schema.sys:
                to_rm = default_fields[:]
            else:
                to_rm = []
            to_rm.append("_id")
            data = await self.mdata.search_export(
                data_model, fields=[], query=query, parent=parent_name, remove_keys=to_rm)
        # logger.info(f"export {len(data)} lines")
        return {
            "content": {
                "mode": "list",
                "model": model_name,
                "schema": schema or {},
                "data": data or [],
            }
        }

    async def update_record_user_data(self, record, uid):
        logger.info(f"update {uid}")
        self.auth_service = ServiceAuth.new(
            public_endpoint=[], parent=self, request=self.request,
            pwd_context=self.pwd_context, req_id="")
        user = await self.auth_service.session_service.user_role(uid)
        logger.info(f"record update {user.get('uid')}")
        record.owner_uid = user.get('uid')
        record.owner_name = user.get('full_name', "")
        record.owner_mail = user.get('mail', "")
        record.owner_sector = user.get("divisione_uo", "")
        record.owner_sector_id = int(user.get('divisione_uo_id', 0))
        record.owner_personal_type = user.get("tipo_personale", "")
        record.owner_job_title = user.get("qualifica", "")
        record.owner_function = user.get("user_function")
        return record

    async def celan_model(self, model_name):
        await self.make_settings()
        if not self.session.is_admin:
            return {
                "status": "error",
                "message": f"Error Admin Only",
                "model": model_name
            }
        data_model = await self.mdata.gen_model(model_name)
        res = await self.mdata.delete_records(data_model, {})
        return {
            "status": "ok",
            "rec_name": "",
            "model": model_name
        }

    async def import_raw_data(self, model_name, record_data):
        await self.make_settings()
        if not self.session.is_admin:
            return {
                "status": "error",
                "message": f"Admin Only",
                "model": model_name
            }
        data_model = await self.mdata.gen_model(model_name)
        try:
            record = data_model(**record_data)
            create_add_user = True
            if record_data.get("owner_uid"):
                record = await self.update_record_user_data(
                    record, record_data.get("owner_uid")
                )
                if not record.owner_uid:
                    return {
                        "status": "error",
                        "message": f"Errore validazione {record_data.get('owner_uid')} ",
                        "model": model_name
                    }
                create_add_user = False
            object_o = await self.mdata.save_object(
                self.session, record, model_name=model_name, copy=False, create_add_user=create_add_user)

            if isinstance(object_o, dict):
                return object_o
            return {
                "status": "ok",
                "rec_name": object_o.rec_name,
                "model": model_name
            }
        except ValidationError as e:
            logger.error(f" Validation {e}")
            return {
                "status": "error",
                "message": f"Errore validazione {e}",
                "model": model_name
            }

    async def get_mail_template(self, model_name, template_name=""):
        logger.info(f" model:{model_name}, template_name:{template_name}")
        # data_mode = json |
        await self.make_settings()

        template_model = await self.mdata.gen_model("mail_template")
        query = {"$and": [{"model": model_name}, {"default": True}]}
        if template_name:
            query = {"rec_name": template_name}
        query = await self.qe.default_query(template_model, query)

        list_template = await self.mdata.search(template_model, query=query)
        tmp_dict = {}
        if list_template:
            tmp_dict = list_template[0]

        return {
            "content": {
                "mode": "form",
                "model": model_name,
                "data": tmp_dict or {},
            }
        }

    async def get_mail_server_out(self, server_name=""):
        logger.info(f" server_name:{server_name}")
        await self.make_settings()
        server_model = await self.mdata.gen_model("mail_server_out")

        query = await self.qe.default_query(server_model, {"rec_name": server_name})

        list_server = await self.mdata.search(server_model, query=query)
        server_dict = {}
        if list_server:
            server_dict = list_server[0]

        return {
            "content": {
                "mode": "form",
                "model": "mail_server_out",
                "data": server_dict or {},
            }
        }

    async def attachment_to_trash(self, model_name, rec_name, data):
        logger.info(f"model:{model_name}, rec_name:{rec_name} data {data}")
        # data_mode = json | value
        await self.make_settings()
        try:
            key = data.get('key')
            file_field = data.get("field")

            data_model = await self.mdata.gen_model(model_name)
            trash_model = await self.mdata.gen_model("attachment_trash")
            record = await self.mdata.by_name(
                data_model, record_name=rec_name)
            record_dict = record.dict()

            list_files = []
            rec_to_save = []
            for file_todo in record_dict[file_field]:
                if not file_todo['key'] == key:
                    list_files.append(file_todo)
                else:
                    rec_to_save.append(file_todo)
            record_dict[file_field] = list_files[:]
            new_record = data_model(**record_dict)

            trash = trash_model(**{
                "rec_name": f"trash.{str(uuid.uuid4())}",
                "model": model_name,
                "model_rec_name": rec_name,
                "attachments": rec_to_save[:],
            })
            record = await self.mdata.save_object(
                self.session, trash, rec_name="", model_name="attachment_trash")
            # if error record is dict
            if isinstance(record, dict):
                return record
            await self.mdata.save_record(new_record)
            return {
                "link": "#",
                "reload": True
            }
        except Exception as e:
            logger.error(e, exc_info=True)
            return {
                "status": "error",
                "message": f"Errore  {e}",
                "model": model_name,
                "rec_name": rec_name
            }

    async def clean_all_to_delete_action(self):
        logger.info(f"clean expired to_delete_action ")
        await self.make_settings()
        return await self.mdata.clean_expired_to_delete_record()

    async def get_calendar_task(self, task_name) -> dict:
        await self.make_settings()
        try:
            calendar = await self.mdata.by_name("calendar", task_name)
            task = await self.mdata.by_name("action", calendar.task)
            return {
                "status": "success",
                "calendar": calendar,
                "task": task
            }
        except Exception as e:
            logger.error(f"Task: {task_name} - {e}", exc_info=True)
            return {"status": "error", "data": {}, "name": task_name}

    async def update_calendar_task(self, task_name, execution_status) -> dict:
        action = await self.mdata.by_name("action", task_name)
        can_read = await self.acl.can_read(action)
        if not can_read:
            return {
                "status": "error",
                "name": task_name,
                "data": {}
            }
        await self.make_settings()
        try:
            calendar = await self.mdata.by_name("calendar", task_name)
            task = await self.mdata.by_name("action", calendar.task)
            self.action_service = ServiceAction.new(
                session=self.session, service_main=self, action_name=task.rec_name,
                rec_name=calendar.rec_name, parent="", iframe="", execute=True,
                pwd_context=self.pwd_context
            )
            await self.action_service.make_settings()
            return await self.action_service.calendar_task(task_name, calendar, task, execution_status)
        except Exception as e:
            logger.error(f"Task: {task_name} - {e}", exc_info=True)
            return {"status": "error", "data": {}, "name": task_name}

    async def count(self, model_name, query_data):
        query = await self.qe.default_query(self.data_model, query_data)
        recordsTotal = await self.mdata.count_by_filter(model_name, query=query)
        return {"total": recordsTotal}

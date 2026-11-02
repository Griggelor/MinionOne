"""Real native model lifecycle adapters; never unload by an inferred display name."""
from .control_http import ControlHTTP
from .worker_profiles import WorkerProviderError


class ModelRuntime:
    def __init__(self,profile,*,http=None):self.profile=profile;self.http=http or ControlHTTP(profile)
    def snapshot(self):
        if self.profile.provider=='unsloth':
            raw=self.http.request('GET','/api/inference/status')
            if (type(raw.get('loaded')) is not list or type(raw.get('loading')) is not list or
                    any(type(x) is not str for x in raw['loaded']+raw['loading'])):
                raise WorkerProviderError('PROVIDER_RESPONSE_INVALID')
            return {'loaded':set(raw['loaded']),'loading':raw['loading'],
                    'active':raw.get('model_identifier'),'instances':{x:x for x in raw['loaded']}}
        raw=self.http.request('GET','/api/v1/models')
        if type(raw.get('models')) is not list:raise WorkerProviderError('PROVIDER_RESPONSE_INVALID')
        instances={}
        for model in raw['models']:
            if type(model) is not dict or type(model.get('key')) is not str or type(model.get('loaded_instances')) is not list:
                raise WorkerProviderError('PROVIDER_RESPONSE_INVALID')
            for instance in model['loaded_instances']:
                if type(instance) is not dict or type(instance.get('id')) is not str:
                    raise WorkerProviderError('PROVIDER_RESPONSE_INVALID')
                # Multiple instances are deliberately not collapsed to a model key.
                instances[instance['id']]=model['key']
        return {'loaded':set(instances),'loading':[],'active':None,'instances':instances}
    def present(self,snapshot):
        if self.profile.provider=='unsloth':return self.profile.model_id in snapshot['loaded']
        return self.profile.model_id in snapshot['loaded'] or self.profile.model_id in snapshot['instances'].values()
    def ready(self,snapshot):
        return self.present(snapshot) and not snapshot['loading'] and (
            self.profile.provider!='unsloth' or snapshot['active']==self.profile.model_id)
    def load(self,operation_id,*,load_options=None):
        before=self.snapshot()
        options=self.profile.load_options if load_options is None else load_options
        if before['loading']:raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
        if self.ready(before):return None
        if self.profile.provider=='unsloth':
            payload={**options,'model_path':self.profile.model_id,
                'load_request_id':operation_id,'alongside':True,'force_alongside':False,'force_reload':False}
            answer=self.http.request('POST','/api/inference/load',payload,timeout=self.profile.load_timeout_seconds)
            if answer.get('evicted') or answer.get('evicted_slots'):raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
            owned=self.profile.model_id if not self.present(before) else None
        else:
            answer=self.http.request('POST','/api/v1/models/load',{'model':self.profile.model_id,**options},
                                     timeout=self.profile.load_timeout_seconds)
            owned=answer.get('instance_id')
            if answer.get('status')!='loaded' or type(owned) is not str:raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        after=self.snapshot()
        if (self.profile.provider=='lmstudio' and
                (owned in before['loaded'] or after['instances'].get(owned)!=self.profile.model_id)):
            raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        if not before['loaded']<=after['loaded'] or not self.ready(after):
            raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        return owned
    def unload(self,instance):
        before=self.snapshot()
        if ((self.profile.provider=='unsloth' and instance!=self.profile.model_id) or
                (self.profile.provider=='lmstudio' and instance in before['loaded'] and before['instances'].get(instance)!=self.profile.model_id)):
            raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        if instance not in before['loaded']:return
        if before['loading']:raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
        if self.profile.provider=='unsloth':
            self.http.request('POST','/api/inference/unload',{'model_path':instance,'force_cancel_active':False},
                              timeout=self.profile.load_timeout_seconds)
        else:self.http.request('POST','/api/v1/models/unload',{'instance_id':instance},timeout=self.profile.load_timeout_seconds)
        after=self.snapshot()
        if instance in after['loaded'] or not (before['loaded']-{instance})<=after['loaded']:
            raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')

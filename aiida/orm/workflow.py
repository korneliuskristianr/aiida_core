import os
import importlib

import aiida.common
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from aiida.common.exceptions import (InternalError, ModificationNotAllowed, NotExistent, ValidationError, AiidaException )
from aiida.common.folders import RepositoryFolder, SandboxFolder
from aiida.common.datastructures import wf_states, wf_exit_call

from aiida.djsite.utils import get_automatic_user
from aiida.common import aiidalogger

logger = aiidalogger.getChild('Workflow')

# Name to be used for the section
_section_name = 'workflow'

# The name of the subfolder in which to put the files/directories added with add_path
_path_subfolder_name = 'path'

class Workflow(object):    
    """
    Base class to represent a workflow. This is the superclass of any workflow implementations,
    and provides all the methods necessary to interact with the database.
    
    The typical use case are workflow stored in the aiida.workflow packages, that are initiated
    either by the user in the shell or by some scripts, and that are monitored by the aiida daemon.

    Workflow can have steps, and each step must contain some calculations to be executed. At the
    end of the step's calculations the workflow is reloaded in memory and the next methods is called.

    """
    
    def __init__(self,**kwargs):
        
            """
            If initialized with an uuid the Workflow is loaded from the DB, if not a new
            workflow is generated and added to the DB following the stack frameworks.
            
            This means that only modules inside aiida.worflows are allowed to implements
            the workflow super calls and be stored. The caller names, modules and files are
            retrieved from the stack.
            """
            from aiida.djsite.utils import get_automatic_user
            from aiida.djsite.db.models import DbWorkflow
            
            self._to_be_stored = True

            uuid = kwargs.pop('uuid', None)
            
            if uuid is not None:
                if kwargs:
                        raise ValueError("If you pass a UUID, you cannot pass any further parameter")
                
                try:
                        self._dbworkflowinstance   = DbWorkflow.objects.get(uuid=uuid)
                        self._to_be_stored         = False
                        
                        #self.logger.info("Workflow found in the database, now retrieved")
                        self._repo_folder = RepositoryFolder(section=_section_name, uuid=self.uuid)
                        
                except ObjectDoesNotExist:
                        raise NotExistent("No entry with the UUID {} found".format(uuid))
                    
            else:
                
                # ATTENTION: Do not move this code outside or encapsulate it in a function
                
                import inspect
                stack = inspect.stack()
                
                
                #cur_fr  = inspect.currentframe()
                #call_fr = inspect.getouterframes(cur_fr, 2)
                
                # Get all the caller data
                caller_frame        = stack[1][0]
                caller_file         = stack[1][1]
                caller_funct        = stack[1][3]
                
                caller_module       = inspect.getmodule(caller_frame)
                caller_module_class = caller_frame.f_locals.get('self', None).__class__
                
                if not caller_funct=="__init__":
                    raise SystemError("A workflow must implement the __init__ class explicitly")
                
                # Test if the launcher is another workflow
                
#                 print "caller_module", caller_module
#                 print "caller_module_class", caller_module_class
#                 print "caller_file", caller_file
#                 print "caller_funct", caller_funct
                  
                # Accept only the aiida.workflows packages
                if caller_module == None or not caller_module.__name__.startswith("aiida.workflows"):
                        raise SystemError("The superclass can't be called directly")
                
                self.caller_module = caller_module.__name__
                self.caller_module_class  = caller_module_class.__name__
                self.caller_file   = caller_file
                self.caller_funct  = caller_funct
                
                self._temp_folder = SandboxFolder()
                self.current_folder.insert_path(self.caller_file, self.caller_module_class)
                # self.store()
                                
                # Test if there are parameters as input
                params = kwargs.pop('params', None)
                
                if params is not None:
                    if type(params) is dict:
                        self._set_parameters(params)
                
            
            self.attach_calc_lazy_storage  = {}
            self.attach_subwf_lazy_storage = {}
            
            
            
            
    ## --------------------------------------
    ##    DB Instance
    ## --------------------------------------

    @property
    def dbworkflowinstance(self):
        from aiida.djsite.db.models import DbWorkflow
        
        if self._dbworkflowinstance.pk is None:
            return self._dbworkflowinstance
        else:
            self._dbworkflowinstance = DbWorkflow.objects.get(pk=self._dbworkflowinstance.pk)
            return self._dbworkflowinstance
    
    def _get_dbworkflowinstance(self):
        return self.dbworkflowinstance
    
    
    ## --------------------------------------
    ##    Folder
    ## --------------------------------------
    
    @property
    def repo_folder(self):
        return self._repo_folder

    @property
    def current_folder(self):
        if self._to_be_stored:
            return self.get_temp_folder()
        else:
            return self.repo_folder

    @property
    def path_subfolder(self):
        return self.current_folder.get_subfolder(
            _path_subfolder_name,reset_limit=True)

    def get_path_list(self, subfolder='.'):
        return self.path_subfolder.get_subfolder(subfolder).get_content_list()

    def get_temp_folder(self):
        if self._temp_folder is None:
            raise InternalError("The temp_folder was asked for node {}, but it is "
                                "not set!".format(self.uuid))
        return self._temp_folder

    def remove_path(self,path):
        """
        Remove a file or directory from the repository directory.

        Can be called only before storing.
        """
        if not self._to_be_stored:
            raise ValueError("Cannot delete a path after storing the node")
        
        if os.path.isabs(path):
            raise ValueError("The destination path in remove_path must be a relative path")
        self.path_subfolder.remove_path(path)

    def add_path(self,src_abs,dst_path):
        """
        Copy a file or folder from a local file inside the repository directory.
        If there is a subpath, folders will be created.

        Copy to a cache directory if the entry has not been saved yet.
        src_abs: the absolute path of the file to copy.
        dst_filename: the (relative) path on which to copy.
        """
        if not self._to_be_stored:
            raise ValueError("Cannot insert a path after storing the node")
        
        if not os.path.isabs(src_abs):
            raise ValueError("The source path in add_path must be absolute")
        if os.path.isabs(dst_path):
            raise ValueError("The destination path in add_path must be a filename without any subfolder")
        self.path_subfolder.insert_path(src_abs,dst_path)


    def get_abs_path(self,path,section=_path_subfolder_name):
        """
        TODO: For the moment works only for one kind of files, 'path' (internal files)
        """
        if os.path.isabs(path):
            raise ValueError("The path in get_abs_path must be relative")
        return self.current_folder.get_subfolder(section,reset_limit=True).get_abs_path(path,check_existence=True)
    
    ## --------------------------------------
    ##    Store and infos
    ## --------------------------------------

    
    @classmethod
    def query(cls,*args,**kwargs):
        """
        Map to the aiidaobjects manager of the DbWorkflow, that returns
        Workflow objects instead of DbWorkflow entities.
        
        """
        from aiida.djsite.db.models import DbWorkflow
        return DbWorkflow.aiidaobjects.filter(*args,**kwargs)
         
    def store(self):
        
        """
        Stores the object data in the database
        """
        
        from aiida.djsite.db.models import DbWorkflow
        import hashlib
        
        
        # This stores the MD5 as well, to test in case the workflow has been modified after the launch 
        self._dbworkflowinstance = DbWorkflow.objects.create(user=get_automatic_user(),
                                                        module = self.caller_module,
                                                        module_class = self.caller_module_class,
                                                        script_path = self.caller_file,
                                                        script_md5 = hashlib.md5(self.caller_file).hexdigest()
                                                        )
        if hasattr(self, '_params'):
            self.dbworkflowinstance.add_parameters(self._params, force=False)
        
        self._repo_folder = RepositoryFolder(section=_section_name, uuid=self.uuid)
        self.repo_folder.replace_with_folder(self.get_temp_folder().abspath, move=True, overwrite=True)
        
        self._temp_folder       = None  
        self._to_be_stored      = False
    
    
    @property
    def uuid(self):
        return self.dbworkflowinstance.uuid
    
    def info(self):
        
        """
        Returns an array with all the informations
        """
        
        return [self.dbworkflowinstance.module,
            self.dbworkflowinstance.module_class, 
            self.dbworkflowinstance.script_path,
            self.dbworkflowinstance.script_md5,
            self.dbworkflowinstance.time,
            self.dbworkflowinstance.status]
    
    # --------------------------------------------
    #         Parameters, attribute, results
    # --------------------------------------------
    
    def _set_parameters(self, params, force=False):
        
        """
        Adds parameters to the workflow that are both stored and used every time
        the workflow engine re-initialize the specific workflow to launch the new methods.  
        """
        if self._to_be_stored:
            self._params = params
        else:
            self.dbworkflowinstance.add_parameters(params, force=force)
    
    def get_parameters(self):
        if self._to_be_stored:
            return self._params
        else:
            return self.dbworkflowinstance.get_parameters()
    
    def get_parameter(self, _name):
        if self._to_be_stored:
            return self._params(_name)
        else:
            return self.dbworkflowinstance.get_parameter(_name)
    
    # ----------------------------
    
    def get_attributes(self):
        return self.dbworkflowinstance.get_attributes()
    
    def get_attribute(self, _name):
        return self.dbworkflowinstance.get_attribute(_name)
    
    def add_attributes(self, _params):
        self.dbworkflowinstance.add_attributes(_params)
    
    def add_attribute(self, _name, _value):
        self.dbworkflowinstance.add_attribute(_name, _value)
    
    # ----------------------------
    
    def get_results(self):
        return self.dbworkflowinstance.get_results()
    
    def get_result(self, _name):
        return self.dbworkflowinstance.get_result(_name)
    
    def add_results(self, _params):
        self.dbworkflowinstance.add_results(_params)
    
    def add_result(self, _name, _value):
        self.dbworkflowinstance.add_result(_name, _value)
    
        
    # ----------------------------
    #         Statuses
    # ----------------------------

    def get_status(self):
        
        return self.dbworkflowinstance.status
    
    def set_status(self, status):
        
        self.dbworkflowinstance.set_status(status)
    
    def is_new(self):
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call, wf_default_call
        return self.dbworkflowinstance.status == wf_states.CREATED
    
    def is_running(self):
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call, wf_default_call
        return self.dbworkflowinstance.status == wf_states.RUNNING
    
    def has_finished_ok(self):
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call, wf_default_call
        return self.dbworkflowinstance.status in [wf_states.FINISHED,wf_states.SLEEP] 
    
    def has_failed(self):
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call, wf_default_call
        return self.dbworkflowinstance.status == wf_states.ERROR
    
    def is_subworkflow(self):
        """
        Return True is this is a subworkflow (i.e., if it has a parent), 
        False otherwise.
        """
        return self.dbworkflowinstance.is_subworkflow()
    
    # ----------------------------
    #         Steps
    # ----------------------------

    def get_step(self,step_method):

        """
        Query the database to return the step object, on which calculations and next step are
        linked. In case no step if found None is returned, useful for loop configuration to
        test whether is the first time the method gets called. 
        """
        
        if isinstance(step_method, basestring):
            step_method_name = step_method
        else:
            
            if not getattr(step_method,"is_wf_step"):
                raise AiidaException("Cannot get step calculations from a method not decorated as Workflow method")
        
            step_method_name = step_method.wf_step_name
        
        if (step_method_name==wf_exit_call):
            raise InternalError("Cannot query a step with name {0}, reserved string".format(step_method_name))            
        
        try:
            step = self.dbworkflowinstance.steps.get(name=step_method_name, user=get_automatic_user())
            return step
        except ObjectDoesNotExist:
            return None

    def get_steps(self, state = None):
        
        if state == None:
            return self.dbworkflowinstance.steps.all()#.values_list('name',flat=True)
        else:
            return self.dbworkflowinstance.steps.filter(status=state)    
    
    def has_step(self,method):
        
        return not self.get_step(method)==None
    
    # ----------------------------
    #         Next
    # ----------------------------
    
    @classmethod
    def step(cls, fun):
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call, wf_default_call
        
        wrapped_method = fun.__name__
        
        # This function gets called only if the method is launched with the execution brakets ()
        # Otherwise, when the methid is addressed in a next() call this never gets called and only the 
        # attributes are added
        def wrapper(cls, *args, **kwargs):
            
            """
            """
            
            # Store the workflow at the first step executed
            if cls._to_be_stored:
                cls.store()
            
            if len(args)>0:
                raise AiidaException("A step method cannot have any argument, use add_attribute to the workflow")
            
            # If a method is launched and the step is RUNNING or INITIALIZED we should stop
            if cls.has_step(wrapped_method) and \
               not (cls.get_step(wrapped_method).status == wf_states.ERROR or \
                    cls.get_step(wrapped_method).status == wf_states.SLEEP or \
                    cls.get_step(wrapped_method).nextcall == wf_default_call or \
                    cls.get_step(wrapped_method).nextcall == wrapped_method \
                    #cls.has_step(wrapped_method) \
                    ):
                
                raise AiidaException("The step {0} has already been initialized, cannot change this outside the parent workflow !".format(wrapped_method))
            
            # If a method is launched and the step is halted for ERROR, then clean the step and re-launch
            if cls.has_step(wrapped_method) and \
               ( cls.get_step(wrapped_method).status == wf_states.ERROR or\
                 cls.get_step(wrapped_method).status == wf_states.SLEEP ):
                
                for w in cls.get_step(wrapped_method).get_sub_workflows(): w.kill()
                cls.get_step(wrapped_method).remove_sub_workflows()
                
                for c in cls.get_step(wrapped_method).get_calculations(): c.kill()
                cls.get_step(wrapped_method).remove_calculations()
                
                #self.get_steps(wrapped_method).set_nextcall(wf_exit_call)
            
            method_step, created = cls.dbworkflowinstance.steps.get_or_create(name=wrapped_method, user=get_automatic_user())    
            
            try:
                fun(cls)
            except:
                
                import sys, os, traceback
                exc_type, exc_value, exc_traceback = sys.exc_info()                
                cls.append_to_report("ERROR ! This workflow got and error in the {0} method, we report down the stack trace".format(wrapped_method))
                cls.append_to_report("full traceback: {0}".format(traceback.format_exc()))
                method_step.set_status(wf_states.ERROR)
            
            return None 
        
        
        out = wrapper
        
        wrapper.is_wf_step = True
        wrapper.wf_step_name = fun.__name__
        
        return wrapper
        
        
    def next(self, next_method):
        
        """
        Add to the database the next step to be called after the completion of the calculation.
        The source step is retrieved from the stack frameworks and the object can be either a string
        or a method.
        """
        
        import hashlib
        md5          = self.dbworkflowinstance.script_md5
        script_path  = self.dbworkflowinstance.script_path
        
        if not md5==hashlib.md5(script_path).hexdigest():
            raise ValidationError("Unable to load the original workflow module from {}, MD5 has changed".format(script_path))
        
        import inspect
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call
 
        # ATTENTION: Do not move this code outside or encapsulate it in a function
        curframe      = inspect.currentframe()
        calframe      = inspect.getouterframes(curframe, 2)
        caller_method = calframe[1][3]
        
        
#         logger.info("We are in next call of {0} in {1}".format(caller_method, self.uuid()))
        
        if next_method is None:
            raise AiidaException("The next method is None, probably you passed a method with parenthesis ??")
             
        if not self.has_step(caller_method):
            raise AiidaException("The caller method is either not a step or has not been registered as one")
        
        if not next_method.__name__== wf_exit_call:
            try:
                is_wf_step = getattr(next_method,"is_wf_step", None)
            except AttributeError:
                raise AiidaException("Cannot add as next call a method not decorated as Workflow method")
        else:
            print "Next is an end call of {0} in {1}".format(caller_method, self.uuid)
            
        # Retrieve the caller method
        method_step = self.dbworkflowinstance.steps.get(name=caller_method, user=get_automatic_user())
        
        # Attach calculations
        if caller_method in self.attach_calc_lazy_storage:
            for c in self.attach_calc_lazy_storage[caller_method]:
                method_step.add_calculation(c)
        
        # Attach sub-workflows
        if caller_method in self.attach_subwf_lazy_storage:
            for w in self.attach_subwf_lazy_storage[caller_method]:
                method_step.add_sub_workflow(w)
        
        # Set the next method
        if not next_method.__name__== wf_exit_call:
            next_method_name = next_method.wf_step_name
        else:
            next_method_name = wf_exit_call
            
        #logger.info("Adding step {0} after {1} in {2}".format(next_method_name, caller_method, self.uuid))
        method_step.set_nextcall(next_method_name)
        
        
        """
        Store the workflow if it has not been done yet. This permits the workflow manager to handle to workflow
        correctly, and should remove the issue of the workflow starting before all the calculations are 
        """
        # 
        self._get_dbworkflowinstance().set_status(wf_states.RUNNING)
        method_step.set_status(wf_states.RUNNING)
        
            
    # ----------------------------
    #         Attachments
    # ----------------------------
    
    def attach_calculation(self, calc):
        
        
        """
        Adds a calculation to the caller step in the database. For a step to be completed all
        the calculations have to be RETRIVED, after which the next methid gets called.
        The source step is retrieved from the stack frameworks.
        """
        
        from aiida.orm import Calculation
        from celery.task import task
        from aiida.djsite.db import tasks

        import inspect

        if (not issubclass(calc.__class__,Calculation) and not isinstance(calc, Calculation)):
            raise AiidaException("Cannot add a calculation not of type Calculation")                        

        curframe = inspect.currentframe()
        calframe = inspect.getouterframes(curframe, 2)
        caller_funct = calframe[1][3]
        
        if not caller_funct in self.attach_calc_lazy_storage:
            self.attach_calc_lazy_storage[caller_funct] = []
        self.attach_calc_lazy_storage[caller_funct].append(calc)
        
    def attach_workflow(self, sub_wf):
        
        from aiida.orm import Calculation
        from celery.task import task
        from aiida.djsite.db import tasks

        import inspect

        curframe = inspect.currentframe()
        calframe = inspect.getouterframes(curframe, 2)
        caller_funct = calframe[1][3]
        
        if not caller_funct in self.attach_subwf_lazy_storage:
            self.attach_subwf_lazy_storage[caller_funct] = []
        self.attach_subwf_lazy_storage[caller_funct].append(sub_wf)
        
    
    # ----------------------------
    #      Subworkflows
    # ----------------------------


    def get_step_calculations(self, step_method, calc_state = None):
        
        """
        Retrieve the calculations connected to a specific step in the database. If the step
        is not existent it returns None, useful for simpler grammatic in the worflow definition.
        """
       
        if not getattr(step_method,"is_wf_step"):
            raise AiidaException("Cannot get step calculations from a method not decorated as Workflow method")
        
        step_method_name = step_method.wf_step_name
        
        try:
            stp = self.get_step(step_method_name)
            return stp.get_calculations(state = calc_state)
        except:
            raise AiidaException("Cannot retrieve step's calculations")
        

    def kill_step_calculations(self, step):
        
        from aiida.common.datastructures import calc_states
            
        for c in step.get_calculations():
            c._set_state(calc_states.FINISHED)
    
    # ----------------------------
    #         Support methods
    # ----------------------------


    def kill(self):
         
        from aiida.common.datastructures import calc_states, wf_states, wf_exit_call
         
        for s in self.get_steps(state=wf_states.RUNNING):
            self.kill_step_calculations(s)
            
            for w in s.get_sub_workflows():
                print "Killing {0}".format(w.uuid)
                w.kill()
        
        self.dbworkflowinstance.set_status(wf_states.FINISHED)
    
    def sleep(self):
        
        import inspect
        from aiida.common.datastructures import wf_start_call, wf_states, wf_exit_call
        from aiida.common.datastructures import calc_states, wf_states
        
        # ATTENTION: Do not move this code outside or encapsulate it in a function
        curframe      = inspect.currentframe()
        calframe      = inspect.getouterframes(curframe, 2)
        caller_method = calframe[1][3]
        
        if not self.has_step(caller_method):
            raise AiidaException("The caller method is either not a step or has not been registered as one")
 
        self.get_step(caller_method).set_status(wf_states.SLEEP)
        
        
    # ------------------------------------------------------
    #         Report
    # ------------------------------------------------------
    
    def get_report(self):
        
        if len(self.dbworkflowinstance.parent_workflow_step.all())==0:
            return self.dbworkflowinstance.report.splitlines()
        else:
            return Workflow.get_subclass_from_uuid(self.dbworkflowinstance.parent_workflow_step.get().parent.uuid).get_report()
    
    def clear_report(self):
        
        if len(self.dbworkflowinstance.parent_workflow_step.all())==0:
            self.dbworkflowinstance.clear_report()
        else:
            Workflow(uuid=self.dbworkflowinstance.parent_workflow_step.get().parent.uuid).clear_report()
            
    
    def append_to_report(self, text):
        
        if len(self.dbworkflowinstance.parent_workflow_step.all())==0:
            self.dbworkflowinstance.append_to_report(text)
        else:
            Workflow(uuid=self.dbworkflowinstance.parent_workflow_step.get().parent.uuid).append_to_report(text)
        
    # ------------------------------------------------------
    #         Retrieval
    # ------------------------------------------------------
    
    @classmethod
    def get_subclass_from_dbnode(cls, wf_db, no_check=False):
        
        """
        Core of the workflow next engine. The workflow is checked against MD5 hash of the stored script, 
        if the match is found the python script is reload in memory with the importlib library, the
        main class is searched and then loaded, parameters are added and the new methid is launched.
        """
        
        from aiida.djsite.db.models import DbWorkflow
        import importlib
        import hashlib
        
        module       = wf_db.module
        module_class = wf_db.module_class
        
         
        try:
            wf_mod = importlib.import_module(module)
        except ImportError:
            raise InternalError("Unable to load the workflow module {}".format(module))
        
        for elem_name, elem in wf_mod.__dict__.iteritems():
            
            if module_class==elem_name: #and issubclass(elem, Workflow):
                return getattr(wf_mod,elem_name)(uuid=wf_db.uuid)
    
    @classmethod      
    def get_subclass_from_pk(cls,pk, no_check=False):
        
        """
        Simple method to use retrieve starting from pk
        """
        
        from aiida.djsite.db.models import DbWorkflow
        
        try:
            
            dbworkflowinstance    = DbWorkflow.objects.get(pk=pk)
            return cls.get_subclass_from_dbnode(dbworkflowinstance, no_check=False)
                  
        except ObjectDoesNotExist:
            raise NotExistent("No entry with pk={} found".format(pk))
                           
    @classmethod      
    def get_subclass_from_uuid(cls,uuid, no_check=False):
        
        """
        Simple method to use retrieve starting from uuid
        """
        
        from aiida.djsite.db.models import DbWorkflow
        
        try:
            
            dbworkflowinstance    = DbWorkflow.objects.get(uuid=uuid)
            return cls.get_subclass_from_dbnode(dbworkflowinstance, no_check=False)
                  
        except ObjectDoesNotExist:
            raise NotExistent("No entry with the UUID {} found".format(uuid))
    
    @classmethod 
    def kill_by_uuid(cls,uuid):
    
        cls.retrieve_by_uuid(uuid).kill()
    
    def exit(self):
        pass
    
    def revive(self):
        
        import hashlib
        md5          = self.dbworkflowinstance.script_md5
        script_path  = self.dbworkflowinstance.script_path
        
        md5_check    = hashlib.md5(script_path).hexdigest()
        
        # MD5 Check before revive
        if not md5==md5_check:
            logger.info("The script has changed, MD5 is now updated")
            self.dbworkflowinstance.set_script_md5(md5_check)
        
        # Clear all the erroneous steps
        err_steps    = self.get_steps(state=wf_states.ERROR)
        for s in err_steps:
            
            for w in s.get_sub_workflows(): w.kill()
            s.remove_sub_workflows()
            
            for c in s.get_calculations(): c.kill()
            s.remove_calculations()
            
            s.set_status(wf_states.INITIALIZED)
            
        self.set_status(wf_states.RUNNING)
    
# ------------------------------------------------------
#         Module functions for monitor and control
# ------------------------------------------------------

def kill_from_pk(pk):
    """
    Kills a workflow without loading the class, useful when there was a problem
    and the workflow definition module was changed/deleted (and the workflow
    cannot be reloaded).
    
    :param pk: the principal key (id) of the workflow to kill
    """
    
    w = Workflow.query(pk=pk)[0].kill()
    
def kill_from_uuid(uuid):
    """
    Kills a workflow without loading the class, useful when there was a problem
    and the workflow definition module was changed/deleted (and the workflow
    cannot be reloaded).
    
    :param uuid: the UUID of the workflow to kill
    """
    
    w = Workflow.query(uuid=uuid)[0].kill()
    
def kill_all():
    
    from aiida.djsite.utils import get_automatic_user
    from aiida.djsite.db.models import DbWorkflow
    from django.db.models import Q
    
    q_object = Q(user=get_automatic_user())
    q_object.add(~Q(status=wf_states.FINISHED), Q.AND)
    w_list = DbWorkflow.objects.filter(q_object)
    
    for w in w_list:
        Workflow.get_subclass_from_uuid(w.uuid, no_check=True).kill()
        


def get_workflow_info(w, tab_size = 2, extended = False, pre_string = ""):
    """
    Return a string with all the information regarding the given workflow and
    all its calculations and subworkflows.
    
    :param w: a DbWorkflow instance
    :param indent_level: the indentation level of this workflow
    :param tab_size: number of spaces to use for the indentation
    :param extended: if True, provide some extra information (not implemented
       yet)
    """
    
    def str_timedelta(dt):
        """
        Given a dt in seconds, return it in a HH:MM:SS format.

        :param dt: a time in seconds
        """        
        s = dt.seconds
        hours, remainder = divmod(s, 3600)
        minutes, seconds = divmod(remainder, 60)
        return '%sh :%sm :%ss' % (hours, minutes, seconds)

    import datetime
    from django.utils.timezone import utc
    
    if tab_size < 2:
        raise ValueError("tab_size must be > 2")
    
    now = datetime.datetime.utcnow().replace(tzinfo=utc)    
    lines = []
    
    lines.append(pre_string + "+ Workflow {0} (pk={1}) is {2} [{3}]".format(
               w.module_class, w.pk, w.status, str_timedelta(now-w.time)))

    steps = w.steps.all()

    for idx, s in enumerate(steps):
        lines.append(pre_string + "|"+'-'*(tab_size-1) +
                     "* Step: {0} (->{1}) is {2}".format(
                         s.name,s.nextcall,s.status))

        # I need the filter to then get the value of the state
        calcs  = s.get_calculations().filter(
             attributes__key="_state").values_list(
             "uuid", "ctime", "attributes__tval", "pk")
            
        for c in calcs:
            lines.append(pre_string + "|" + " "*(tab_size-1) +
                         "| Calculation (pk={0}) is {1}".format(c[3], c[2]))
        ## SubWorkflows
        wflows = s.get_sub_workflows()     
        for subwf in wflows:
            lines.append( get_workflow_info(subwf.dbworkflowinstance,
               extended=extended, tab_size = tab_size,
               pre_string = pre_string + "|" + " "*(tab_size-1)) ) 
        
        if idx != (len(steps) - 1):
            lines.append(pre_string + "|")

    return "\n".join(lines)
    
def list_workflows(extended = False, print_all = False, tab_size = 2):
    """
    This function return a string with a description of the AiiDA workflows.
    
    :param extended: if True, provide some extra information
    :param print_all:  if True, print also workflows that have finished
    :param tab_size: how many spaces to use for indentation of subworkflows
    """
    from aiida.djsite.db.models import DbWorkflow
    from django.db.models import Q
    
    q_object = Q(user=get_automatic_user())
    
    if not print_all:
        q_object.add(~Q(status=wf_states.FINISHED), Q.AND)

    wf_list = DbWorkflow.objects.filter(q_object).order_by('time')
    
    lines = []
    for w in wf_list:
        if not w.is_subworkflow():
            lines.append(get_workflow_info(w, tab_size=tab_size, extended=extended))
    
    # empty line between workflows
    retstring = "\n\n".join(lines)
    if not retstring:
        if print_all:
            retstring = "# No workflows found"
        else:
            retstring = "# No running workflows found"
    return retstring
        
                
    

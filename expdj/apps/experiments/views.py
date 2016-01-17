from django.shortcuts import get_object_or_404, render_to_response, render, redirect
from expdj.apps.experiments.models import ExperimentTemplate, Experiment, Battery, \
 ExperimentVariable, CreditCondition
from expdj.apps.turk.models import HIT, Result
from expdj.apps.experiments.forms import ExperimentForm, ExperimentTemplateForm, BatteryForm
from expdj.apps.experiments.utils import get_experiment_selection, install_experiments, \
  update_credits, make_results_df
from expdj.settings import BASE_DIR,STATIC_ROOT,MEDIA_ROOT
from django.forms.models import model_to_dict
from expfactory.views import embed_experiment
from django.http.response import HttpResponseRedirect, HttpResponseForbidden, Http404
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
import shutil
import numpy
import pandas
import uuid
import json
import csv
import re
import os

media_dir = os.path.join(BASE_DIR,MEDIA_ROOT)

### AUTHENTICATION ####################################################

def owner_or_contrib(request,assessment):
    if not request.user.is_anonymous():
        if owner_or_super(request,assessment) or request.user in assessment.contributors.all():
            return True
    return False

def owner_or_super(request,assessment):
    if not request.user.is_anonymous():
        if assessment.owner == request.user or request.user.is_superuser:
            return True
    return False

def check_experiment_edit_permission(request):
    if request.user.is_superuser:
        return True
    return False

def check_battery_edit_permission(request,battery):
    if not request.user.is_anonymous():
        if request.user == battery.owner or request.user in battery.contributors.all():
            return True
        if request.user.is_superuser:
            return True
    return False

def is_question_editor(request):
    if not request.user.is_anonymous():
        if request.user.is_superuser:
            return True
    return False

def is_behavior_editor(request):
    if request.user.is_superuser:
        return True
    return False


#### GETS #############################################################

# get experiment template
def get_experiment_template(eid,request,mode=None):
    keyargs = {'pk':eid}
    try:
        experiment = ExperimentTemplate.objects.get(**keyargs)
    except ExperimentTemplate.DoesNotExist:
        raise Http404
    else:
        return experiment

# get experiment
def get_experiment(eid,request,mode=None):
    keyargs = {'id':eid}
    try:
        experiment = Experiment.objects.get(**keyargs)
    except Experiment.DoesNotExist:
        raise Http404
    else:
        return experiment

# get battery with experiments
def get_battery(bid,request,mode=None):
    keyargs = {'pk':bid}
    try:
        battery = Battery.objects.get(**keyargs)
    except Battery.DoesNotExist:
        raise Http404
    else:
        return battery


#### VIEWS #############################################################

# View a single experiment
@login_required
def update_experiment_templates(request,eid=None):
    if request.user.is_superuser:
        if eid==None:
            experiments = ExperimentTemplate.objects.all()
        else:
            experiments = [get_experiment_template(request,eid)]
        current_experiments = [e.tag for e in experiments]
        [delete_experiment_template(request, e, do_redirect=False) for e in current_experiments]
        errored_experiments = install_experiments(experiment_tags=current_experiments)
        if len(errored_experiments) > 0:
            message = "The experiments %s did not update successfully." %(",".join(errored_experiments))
        else:
            message = "Experiments updated successfully."
            experiments = ExperimentTemplate.objects.all()
            context = {"experiments":experiments,
                       "message":message}
    return render(request, "all_experiments.html", context)



# View a single experiment
@login_required
def view_experiment(request, eid, bid=None):

    # Determine permissions for edit and deletion
    edit_permission = check_experiment_edit_permission(request)
    delete_permission = edit_permission

    # View an experiment associated with a battery
    if bid:
        experiment = get_experiment(eid,request)
        battery = get_battery(bid,request)
        edit_permission = check_battery_edit_permission(request,battery)
        delete_permission = edit_permission
        template = 'experiment_details.html'

    # An experiment template
    else:
        experiment = get_experiment_template(eid,request)
        template = 'experiment_template_details.html'
        battery = None

    context = {'experiment': experiment,
               'edit_permission':edit_permission,
               'delete_permission':delete_permission,
               'battery':battery}

    return render_to_response(template, context)

# View a battery
@login_required
def view_battery(request, bid):
    battery = get_battery(bid,request)

    # Get associated HITS
    hits = HIT.objects.filter(battery=battery)

    # Determine permissions for edit and deletion
    edit_permission = check_battery_edit_permission(request,battery)
    delete_permission = check_battery_edit_permission(request,battery)

    # Check if battery has results
    has_results = False
    if len(Result.objects.filter(assignment__hit__battery=battery)) > 0:
        has_results = True

    context = {'battery': battery,
               'edit_permission':edit_permission,
               'delete_permission':delete_permission,
               'hits':hits,
               'has_results':has_results}

    return render(request,'battery_details.html', context)


# All experiments
@login_required
def experiments_view(request):
    experiments = ExperimentTemplate.objects.all()
    delete_permission = check_experiment_edit_permission(request)
    context = {'experiments': experiments,
               'delete_permission':delete_permission}
    return render(request, 'all_experiments.html', context)


# All batteries
@login_required
def batteries_view(request,uid=None):
    if not uid:
        batteries = Battery.objects.all()
    else:
        batteries = Battery.objects.filter(owner_id=uid)
    context = {'batteries': batteries}
    return render(request, 'all_batteries.html', context)


# Previews -------------------------------------------------------------
# Preview experiments - right now just for templates
@login_required
def preview_experiment(request,eid):
    experiment = get_experiment_template(eid,request)
    experiment_folder = os.path.join(media_dir,"experiments",experiment.tag)
    experiment_html = embed_experiment(experiment_folder,url_prefix="/")
    context = {"preview_html":experiment_html}
    return render_to_response('experiment_preview.html', context)


#### EDIT/ADD/DELETE ###################################################

# ExperimentTemplates ----------------------------------------------------------

@login_required
def add_experiment_template(request):
    '''add_experiment_template
    View for presenting available experiments to user (from expfactory-experiements repo)
    '''
    experiment_selection = get_experiment_selection()
    current_experiments = ExperimentTemplate.objects.all()
    tags = [e.tag for e in current_experiments]
    newexperiments = [e for e in experiment_selection if e["tag"] not in tags]
    context = {"newexperiments": newexperiments,
               "experiments": current_experiments}
    return render(request, "add_experiment_template.html", context)

@login_required
def save_experiment_template(request):
    '''save_experiments template
    view for actually adding new experiments (files, etc) to application and database
    '''
    newexperiments = request.POST.keys()
    experiment_selection = get_experiment_selection()
    selected_experiments = [e["tag"] for e in experiment_selection if e["tag"] in newexperiments]
    errored_experiments = install_experiments(experiment_tags=selected_experiments)
    if len(errored_experiments) > 0:
        message = "The experiments %s did not install successfully." %(",".join(errored_experiments))
    else:
        message = "Experiments installed successfully."
    experiments = ExperimentTemplate.objects.all()
    context = {"experiments":experiments,
               "message":message}
    return render(request, "all_experiments.html", context)

@login_required
def edit_experiment_template(request,eid=None):
    '''edit_experiment_template
    view for editing a single experiment. Likely only will be useful to change publication status
    '''
    # Editing an existing experiment
    if eid:
        experiment = get_experiment_template(eid,request)
    else:
        return HttpResponseRedirect("add_experiment_template")

    if request.method == "POST":
        form = ExperimentTemplateForm(request.POST, instance=experiment)

        if form.is_valid():
            experiment = form.save(commit=False)
            experiment.save()

            context = {
                'experiment': experiment.name,
            }
            return HttpResponseRedirect(experiment.get_absolute_url())
    else:
        form = ExperimentTemplateForm(instance=experiment)

    context = {"form": form,
               "experiment":experiment}
    return render(request, "edit_experiment_template.html", context)

# Delete an experiment
@login_required
def delete_experiment_template(request, eid, do_redirect=True):
    experiment = get_experiment_template(eid,request)
    if check_experiment_edit_permission(request):
        # Static Files
        static_files_dir = os.path.join(media_dir,"experiments",experiment.tag)
        shutil.rmtree(static_files_dir)
        # Cognitive Atlas Task
        task = experiment.cognitive_atlas_task
        try:
            if experiment.cognitive_atlas_task.experiment_set.count() == 1:
                # We might want to delete concepts too? Ok for now.
                task.delete()
        except:
            pass
        experiment.delete()

    if do_redirect == True:
        return redirect('experiments')


# Experiments ----------------------------------------------------------

@login_required
def edit_experiment(request,bid,eid):
    '''edit_experiment
    view to edit experiment already added to battery
    '''
    battery = get_battery(bid,request)
    experiment = get_experiment(eid,request)

    if request.method == "POST":
        form = ExperimentForm(request.POST, instance=experiment)

        if form.is_valid():
            experiment = form.save(commit=False)
            experiment.save()
            for cc in experiment.credit_conditions:
                update_credits(experiment,cc.id)
            return HttpResponseRedirect(battery.get_absolute_url())
    else:
        form = ExperimentForm(instance=experiment)

    context = {"form": form,
               "experiment":experiment,
               "battery":battery}
    return render(request, "edit_experiment.html", context)

@login_required
def save_experiment(request,bid):
    '''save_experiment
    save experiment and custom details for battery
    '''
    if request.method == "POST":
        vars = request.POST.keys()
        battery = get_battery(bid,request)
        template = get_experiment_template(request.POST["experiment"],request)
        expression = re.compile("[0-9]+")
        experiment_vids = numpy.unique([expression.findall(x)[0] for x in vars if expression.search(x)]).tolist()

        # Create a credit condition for each experiment variable
        credit_conditions = []
        include_bonus = False
        include_catch = False

        for vid in experiment_vids:
            # Assume that adding the credit condition means the user wants them turned on
            if int(vid) == template.performance_variable.id:
                include_bonus = True
            if int(vid) == template.rejection_variable.id:
                include_catch = True
            experiment_variable = ExperimentVariable.objects.filter(id=vid)[0]
            variable_value = request.POST["val%s" %(vid)] if "val%s" %(vid) in vars else None
            variable_operator = request.POST["oper%s" %(vid)] if "oper%s" %(vid) in vars else None
            variable_amount = request.POST["amt%s" %(vid)] if "amt%s" %(vid) in vars else None
            credit_condition,_ = CreditCondition.objects.update_or_create(variable=experiment_variable,
                                                                          value=variable_value,
                                                                          operator=variable_operator,
                                                                          amount=variable_amount)
            credit_condition.save()
            credit_conditions.append(credit_condition)

        # Create the experiment to add to the battery
        experiment,_ = Experiment.objects.get_or_create(template=template,
                                                        include_bonus=include_bonus,
                                                        include_catch=include_catch)
        experiment.save()
        experiment.credit_conditions=credit_conditions
        experiment.save()

        # Add to battery, will replace old version if it exists
        current_experiments = [e for e in battery.experiments.all() if e.template.tag not in template.tag]
        current_experiments.append(experiment)
        battery.experiments = current_experiments
        battery.save()

    return HttpResponseRedirect(battery.get_absolute_url())

@login_required
def add_experiment(request,bid,eid=None):
    '''add_experiment
    View for presenting available experiments to user to install to battery
    '''
    battery = get_battery(bid,request)
    newexperiments = [x for x in ExperimentTemplate.objects.all() if x not in battery.experiments.all()]

    # Capture the performance and rejection variables appropriately
    # We should be able to look up by tag
    experimentsbytag = dict()
    for newexperiment in newexperiments:
        newexperimentjson = model_to_dict(newexperiment)
        if newexperiment.performance_variable:
            newexperimentjson["performance_variable"] = model_to_dict(newexperiment.performance_variable)
        if newexperiment.rejection_variable:
            newexperimentjson["rejection_variable"] = model_to_dict(newexperiment.rejection_variable)
        experimentsbytag[newexperimentjson["tag"]] = newexperimentjson

    context = {"newexperiments": newexperiments,
               "newexperimentsjson":json.dumps(experimentsbytag),
               "bid":battery.id}
    return render(request, "add_experiment.html", context)

@login_required
def remove_experiment(request,bid,eid):
   '''remove_experiment
   removes an experiment from a battery
   '''
   battery = get_battery(bid,request)
   experiment = get_experiment(eid,request)
   if check_battery_edit_permission(request,battery):
       battery.experiments = [x for x in battery.experiments.all() if x.id != experiment.id]
   battery.save()

   # If experiment is not linked to other batteries, delete it
   if len(Battery.objects.filter(experiments__id=experiment.id)) == 0:
       experiment.delete()
   return HttpResponseRedirect(battery.get_absolute_url())

# Conditions -----------------------------------------------------------

@login_required
def remove_condition(request,bid,eid,cid):
    '''remove_condition: removes a condition from being associated with a battery
    '''
    battery = get_battery(bid,request)
    experiment = get_experiment(eid,request)
    credit_condition = CreditCondition.objects.filter(id=cid)[0]
    experiment.credit_conditions = [c for c in experiment.credit_conditions.all() if c != credit_condition]

    # Delete credit condition if not attached to experiments
    if len(Experiment.objects.filter(credit_conditions__id=cid)) == 0:
        credit_condition.delete()

    # Deletes condition from experiments, if not used from database, turns bonus/rejection on/off
    update_credits(experiment,cid)

    form = ExperimentForm(instance=experiment)

    context = {"form": form,
               "experiment":experiment,
               "battery":battery}
    return render(request, "edit_experiment.html", context)


# Battery --------------------------------------------------------------

@login_required
def add_battery(request):
    '''add_battery
    Function for adding new battery to database
    '''
    return redirect('batteries')

@login_required
def edit_battery(request, bid=None):
    header_text = "Add new battery"
    if bid:
        battery = get_battery(bid,request)
        is_owner = battery.owner == request.user
        header_text = battery.name
        if not request.user.has_perm('battery.edit_battery', battery):
            return HttpResponseForbidden()
    else:
        is_owner = True
        battery = Battery(owner=request.user)
    if request.method == "POST":
        if is_owner:
            form = BatteryForm(request.POST,instance=battery)
        if form.is_valid():
            previous_contribs = set()
            if form.instance.pk is not None:
                previous_contribs = set(form.instance.contributors.all())
            battery = form.save(commit=False)
            battery.save()

            if is_owner:
                form.save_m2m()  # save contributors
                current_contribs = set(battery.contributors.all())
                new_contribs = list(current_contribs.difference(previous_contribs))

            return HttpResponseRedirect(battery.get_absolute_url())
    else:
        if is_owner:
            form = BatteryForm(instance=battery)
        else:
            form = BatteryForm(instance=battery)

    context = {"form": form,
               "is_owner": is_owner,
               "header_text":header_text}

    return render(request, "edit_battery.html", context)


# Delete a battery
@login_required
def delete_battery(request, bid):
    battery = get_battery(bid,request)
    if request.user.has_perm('battery.delete_battery', battery):
        battery.delete()
    return redirect('batteries')



#### EXPORT #############################################################

# Export specific experiment data
@login_required
def export_battery(request,bid):
    battery = get_battery(bid,request)
    output_name = "expfactory_battery_%s.tsv" %(battery.id)
    return export_experiments(battery,output_name)

# Export specific experiment data
@login_required
def export_experiment(request,eid):
    battery = Battery.objects.filter(experiments__id=eid)[0]
    experiment = get_experiment(eid,request)
    output_name = "expfactory_experiment_%s.tsv" %(experiment.template.tag)
    return export_experiments(battery,output_name,[experiment.template.tag])

# General function to export some number of experiments
def export_experiments(battery,output_name,experiment_tags=None):

    # Get all results associated with Battery
    results = Result.objects.filter(assignment__hit__battery=battery)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="%s"' %(output_name)
    writer = csv.writer(response,delimiter='\t')

    # Make a results pandas dataframe
    df = make_results_df(battery,results)

    # Specifying individual experiments removes between trial stufs
    if experiment_tags != None:
        if isinstance(experiment_tags,str):
            experiment_tags = [experiment_tags]
        df = df[df.experiment_tag.isin(experiment_tags)]

    # The program reading in values should fill in appropriate nan value
    df[df.isnull()]=""

    # Write header
    writer.writerow(df.columns.tolist())

    for row in df.iterrows():
        try:
            values = row[1].tolist()
            writer.writerow(values)
        except:
            pass

    return response

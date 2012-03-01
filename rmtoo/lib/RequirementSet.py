'''
 rmtoo
   Free and Open Source Requirements Management Tool
   
  Collection of topics.
  Note that the TopicSet is a tree where the leaves are 
  orders - i.e. it is not possible to put them into a set_value.
   
 (c) 2010-2011 by flonatel GmhH & Co. KG

 For licensing details see COPYING
'''

import os
import re
import sys
import time
import codecs
import operator
import StringIO
import json

from rmtoo.lib.Requirement import Requirement
from rmtoo.lib.Constraint import Constraint
from rmtoo.lib.digraph.Digraph import Digraph
from rmtoo.lib.logging.MemLogStore import MemLogStore
from rmtoo.lib.storagebackend.RecordEntry import RecordEntry
from rmtoo.lib.logging.EventLogging import tracer
from rmtoo.lib.UsableFlag import UsableFlag
from rmtoo.lib.CE3Set import CE3Set
from rmtoo.lib.CE3 import CE3
from rmtoo.lib.RMTException import RMTException
from rmtoo.lib.digraph.TopologicalSort import topological_sort
from rmtoo.lib.FuncCall import FuncCall

class RequirementSet(Digraph, MemLogStore, UsableFlag):
    '''A RequirementSet holds one DAG (directed acyclic graph)
       of requirements.'''

    def __init__(self, config):
        '''Constructs a RequirementSet.
           This does not read everything in: please
           use the appropriate method to do so.'''
        tracer.debug("Called.")
        Digraph.__init__(self)
        MemLogStore.__init__(self)
        UsableFlag.__init__(self)
        self._config = config
        self.__master_nodes = None
        self.__requirements = {}
        self.__ce3set = None
        tracer.debug("Finished.")

    def __str__(self):
        return "Master nodes [%s]  Requirements [%s]" % \
            (self.__master_nodes, self.__requirements)

    def __read_one_requirement(self, fileinfo, input_mods, object_cache):
        '''Read in one requirement from the file info.'''
        tracer.debug("Called.")
        # Check for correct filename
        if not fileinfo.get_filename().endswith(".req"):
            tracer.info("skipping file [%s]" % fileinfo.get_filename())
            return
        # Handle caching.
        vcs_id = fileinfo.get_vcs_id()
        rid = fileinfo.get_filename_sub_part()[:-4]
        req = object_cache.get("Requirement", vcs_id)
        tracer.info("Reading requirement [%s]" % rid)

        if req == None:
            file_content = fileinfo.get_content()
            req = Requirement(file_content, rid, fileinfo.get_filename(),
                              self, input_mods, self._config)
            # Add the requirement to the cache.
            object_cache.add(vcs_id, "Requirement", req)

        self._adapt_usablility(req)

        if req.is_usable():
            # Store in the map, so that it is easy to access the
            # node by id.
            self.__add_requirement(req)
            # Also store it in the digraph's node list for simple
            # access to the digraph algorithms.
            # TODO: self.nodes.append(req) ?
        else:
            self.error(45, "could not be parsed", req.id)
        tracer.debug("Finished.")

    def __read_all_requirements(self, input_handler, commit, input_mods,
                                object_cache):
        '''Read in all the requirements from the input handler.'''
        tracer.debug("Called.");
        fileinfos = input_handler.get_file_infos(commit, "requirements")
        for fileinfo in fileinfos:
            self.__read_one_requirement(fileinfo, input_mods, object_cache)
        tracer.debug("Finished.");

    def __handle_modules_reqdeps(self, input_mods):
        '''This is mostly the same functionality of similar method of the
           class Requirement - but with one major difference: for this
           implementation stop if an error occurred.'''
        for module in input_mods.reqdeps_sorted:
            state = module.rewrite(self)
            if state == False:
                # Some semantic error occurred.
                self._set_not_usable()
                # Do not continue - return immediately, because some
                # algorithms rely on the correct run from others.
                return

    def __all_tags_handled(self):
        '''Returns true iff all the different tags are handled.'''
        all_handled = True
        for req in self.nodes:
            if len(req.brmo) > 0:
                self.error(57, "No tag handler found for tag(s) '%s' "
                           "- Hint: typo in tag(s)?" % req.brmo.keys(),
                           req.get_id())
                all_handled = False
        return all_handled

    def __handle_modules(self, input_mods):
        '''Handle all modules which are executed on the 
           requirement set level.'''
        tracer.debug("Called.")
        # Dependencies can be done, if all requirements are successfully
        # read in.
        self.__handle_modules_reqdeps(input_mods)
        # If there was an error, the state flag is set:
        tracer.debug("Check usability.")
        if not self.is_usable():
            self.error(43, "there was a problem handling the "
                       "requirement set modules")
            return False

        # The must no be left
        tracer.debug("Check all handled.")
        if not self.__all_tags_handled():
            self.error(56, "There were errors encountered during parsing "
                       "and checking - can't continue.")
            return False

        return True

    def read_requirements(self, input_handler, commit, input_mods,
                          object_cache):
        '''Reads in all the requirements from the input_handler.'''
        tracer.debug("Called.")
        self.__read_all_requirements(input_handler, commit, input_mods,
                                     object_cache)
        self.__handle_modules(input_mods)
        tracer.debug("Finished.")

    def __add_requirement(self, req):
        '''Add requirement to the internal container.'''
        tracer.debug("Add requirement [%s]" % req.get_id())
        self.__requirements[req.get_id()] = req

    def restrict_to_topics(self, topic_set):
        '''Restrict the list (dictionary) of requirements to the given
           topic set - i.e. only requirements are returned which belong to
           one of the topics in the topic set.'''
        restricted_reqs = RequirementSet(self._config)
        for req in self.__requirements.values():
            if req.get_topic() in topic_set:
                # Add to the internal map
                restricted_reqs.__add_requirement(req)
                # Add to the common digraph structure
                restricted_reqs.add_node(req)
        return restricted_reqs

    def execute(self, executor, func_prefix):
        '''Execute the parts which are needed for RequirementSet.'''
        tracer.debug("calling pre")
        FuncCall.pcall(executor, func_prefix + "requirement_set_pre", self)
        tracer.debug("calling sub requirement set")
        for requirement in executor.requirement_set_sort(
                            self.__requirements.values()):
            requirement.execute(executor, func_prefix)
        tracer.debug("calling post")
        FuncCall.pcall(executor, func_prefix + "requirement_set_post", self)
        tracer.debug("finished")

    def __resolve_solved_by_one_req(self, req):
        '''Resolve the 'Solved by' for one requirement.'''
        tracer.debug("Called: requirement id [%s]." % req.get_id())

        # Add node to digraph
        self.add_node(req)

        # It is a 'normal' case when there is no 'Solved by' (until now).
        if "Solved by" not in req.brmo:
            return True

        content = req.brmo["Solved by"].get_content()
        # If available, it must not empty
        if len(content) == 0:
            self.error(77, "'Solved by' field has length 0", req.id)
            return False

        # Step through the list
        dep_list = content.split()
        tracer.debug("dependent list [%s]" % dep_list)
        for dep in dep_list:
            if dep not in self.__requirements:
                self.error(74, "'Solved by' points to a "
                           "non-existing requirement '%s'" % dep, req.get_id())
                return False
            # It is not allowed to have self-references: it does not
            # make any sense, that a requirement references itself.
            if dep == req.id:
                self.error(75, "'Solved by' points to the "
                           "requirement itself", req.id)
                return False

            # Mark down the depends on...
            dep_req = self.__requirements[dep]
            # This is exactly the other way as used in the 'Depends on'
            Digraph.create_edge(dep_req, req)

        # Delete the original tag
        del req.brmo["Solved by"]
        return True

    def resolve_solved_by(self):
        '''Step through the internal list of collected requirements and
           evaluate the 'Solved by'.  This is done by creating the
           appropriate digraph nodes.'''
        tracer.debug("Called.")
        # Run through all the requirements and look for the 'Solved
        # by'
        success = True
        for req in self.__requirements.values():
            if not self.__resolve_solved_by_one_req(req):
                success = False
        tracer.debug("Finished; success [%s]." % success)
        return success

    def __resolve_depends_on_one_req(self, req, also_solved_by):
        tracer.debug("Called.")
        # Add node to digraph
        self.add_node(req)

        if req.get_value("Type") == Requirement.rt_master_requirement:
            # There must no 'Depends on'
            if "Depends on" in req.brmo:
                print("+++ ERROR %s: initial requirement has "
                      "Depends on field." % (req.id))
                return False
            # It self does not have any depends on nodes
            req.graph_depends_on = None
            # This is the master!
            return True

        # For all other requirements types there must be a 'Depends on'
        if "Depends on" not in req.brmo:
            if also_solved_by:
                # Skip handling this requirement
                return True
            print("+++ ERROR %s: non-initial requirement has "
                  "no 'Depends on' field." % (req.id))
            return False

        t = req.brmo["Depends on"]

        # If available, it must not empty
        if len(t.get_content()) == 0:
            print("+++ ERROR %s: 'Depends on' field has len 0" %
                  (req.id))
            return False

        # Step through the list
        tl = t.get_content().split()
        for ts in tl:
            if ts not in self.get_all_requirement_ids():
                self.error(47, "'Depends on' points to a "
                             "non-existing requirement '%s'" % ts, req.id)
                return False
            # It is not allowed to have self-references: it does not
            # make any sense, that a requirement references itself.
            if ts == req.id:
                self.error(59, "'Depends on' points to the "
                      "requirement itself", req.id)
                return False

            # Mark down the depends on...
            dep_req = self.__requirements[ts]
            # This is exactly the other way as used in the 'Depends on'
            tracer.debug("Add edge [%s] -> [%s]" %
                         (dep_req.get_id(), req.get_id()))
            Digraph.create_edge(dep_req, req)

        # Copy and delete the original tag
        ## XXX Not neede any more? req.tags["Depends on"] = t.split()
        del req.brmo["Depends on"]
        return True

    def resolve_depends_on(self, also_solved_by):
        '''Step through the internal list of collected requirements and
           evaluate the 'Depends on'.  This is done by creating the 
           appropriate digraph node.'''
        tracer.debug("Called.")
        # Run through all the requirements and look for the 'Depend
        # on' (depending on the type of the requirement)
        success = True
        for req in self.__requirements.values():
            if not self.__resolve_depends_on_one_req(req, also_solved_by):
                success = False
        tracer.debug("Finished; success [%s]." % success)
        return success

    def __create_local_ce3s(self):
        '''Create the local Constraint Execution Environments
           and evaluate the given statements.
           This method does two things:
           - evaluating the constraints in the CE3
           - Resetting the 'Constraints' entry in the requirement
           (instead of the TextRecord a map of name to constraint
            object is stored).'''
        tracer.debug("Called.")
        self.__ce3set = CE3Set()
        for req_name, req in self.__requirements.items():
            ce3 = CE3()
            cstrnts = req.get_value("Constraints")
            if cstrnts != None:
                sval = json.loads(cstrnts.get_content())
                cs = {}
                for s in sval:
                    ctr_name = self.get_ctr_name(s)
                    if not ctr_name in reqset.constraints:
                        raise RMTException(88, "Constraint [%s] does not "
                                           "exists" % ctr_name)
                    rcs = reqset.constraints[ctr_name]
                    ce3.eval(rcs, ctr_name, s)
                    cs[ctr_name] = rcs
                req.set_value("Constraints", cs)
            # Store the fresh create CE3 into the ce3set
            self.__ce3set.insert(req_name, ce3)
        tracer.debug("Finished.")

    def __unite_ce3s(self):
        '''Execute the unification of the CE3s:
           From the list of all incoming nodes and the value of the 
           current node compute the new value of the current node
           The ce3s must be executed in topological order.'''
        ce3tsort = topological_sort(self)
        for r in ce3tsort:
            # Have a look for incoming nodes
            ince3s = []
            for i in r.outgoing:
                ince3s.append(self.__ce3set.get(i.get_id()))
            lce3 = self.__ce3set.get(r.get_id())
            lce3.unite(ince3s)

    def resolve_ce3(self):
        '''Handle the Constraint Execution Environments for this
           requirement set.'''
        tracer.debug("Called.")
        # The first step is to create local Constraint Execution Environments
        self.__create_local_ce3s()
        # Evaluate all the CE3 in topological order
        self.__unite_ce3s()
        tracer.debug("Finished.")

    def find_master_nodes(self):
        '''Find all the available master nodes and stored them in
           a class field.'''
        self.__master_nodes = set()
        for req in self.nodes:
            if len(req.outgoing) == 0:
                tracer.debug("Found master nodes [%s]" % req.get_id())
                self.__master_nodes.add(req)
        tracer.info("Found [%d] master nodes" % len(self.__master_nodes))

    def get_master_nodes(self):
        '''Return the available master nodes.'''
        return self.__master_nodes

    def get_requirements_cnt(self):
        '''Returns the number of requirements.'''
        return len(self.__requirements)

    def get_all_requirement_ids(self):
        '''Return all requirement ids of the requirement set.'''
        return self.__requirements.keys()

    def get_requirement(self, rid):
        '''Return the requirement with the given id.'''
        return self.__requirements[rid]

    def get_requirements_iteritems(self):
        '''Return the iteritems() iterator of all requirements.'''
        return self.__requirements.iteritems()

    def get_ce3set(self):
        '''Return the ce3 set which belongs to this requirement set.'''
        return self.__ce3set

    # EVERYTHING BENEATH IS DEPRECATED!

    deprecated__er_fine = 0
    deprecated__er_error = 1

    def deprecated__init__(self, mods, config):
        self.reqs = {}
        self.mods = mods
        # The requirement set is only (fully) usable, when everything
        # is fine.
        self.state = self.er_fine
        self.config = config
        self.version_id = None

        # The analytic modules store the results in this map:
        self.analytics = {}
        self.constraints = {}

    # Read the whole requirement set from files stored in the
    # filesystem (which is typically the latest version when a repo is
    # available). 
    def deprecated_read_from_filesystem(self, directory):
        everythings_fine = self.read(directory)
        if not everythings_fine:
            self.error(44, "There were errors in the requirment set")
            self.state = self.er_error
            return False

        everythings_fine = self.read_constraints(
            self.config.get_value('constraints.search_dirs'))
        if not everythings_fine:
            self.error(86, "There were errors in the requirment set - "
                       "in the constraints")
            self.state = self.er_error
            return False

        return self.handle_modules()

    # Add requirement: this is needed when reading from VCS.
    def deprecated_add_req(self, req):
        # Store in the map, so that it is easy to access the
        # node by id.
        self.reqs[req.id] = req
        # Also store it in the digraph's node list for simple
        # access to the digraph algorithms.
        self.nodes.append(req)

    def deprecated_read(self, directory):
        everythings_fine = True
        files = os.listdir(directory)
        for f in files:
            m = re.match("^.*\.req$", f)
            if m == None:
                continue
            rid = f[:-4]
            fd = codecs.open(os.path.join(directory, f), "r", "utf-8")
            req = Requirement(fd, rid, self, self.mods, self.config)
            if req.ok():
                # Store in the map, so that it is easy to access the
                # node by id.
                self.reqs[req.id] = req
                # Also store it in the digraph's node list for simple
                # access to the digraph algorithms.
                self.nodes.append(req)
            else:
                self.error(45, "could not be parsed", req.id)
                everythings_fine = False
        self.ts = time.time()
        return everythings_fine

    # This is mostly a copy of the read - but changed at at least some
    # major points. 
    def deprecated_read_constraints(self, directories):
        everythings_fine = True
        for da in directories:
            # TODO: Check if this is really unicode
            # d = unicode(da, "utf-8")
            d = da
            if not os.path.isdir(d):
                print("+++ WARN: skipping non-existant constraint "
                      "directory [%s]" % d)
                continue
            ef = self.read_constraints_one_dir(d)
            if not ef:
                everythings_fine = False
        return everythings_fine

    def deprecated_read_constraints_one_dir(self, directory):
        everythings_fine = True
        files = os.listdir(directory)
        for f in files:
            m = re.match("^.*\.ctr$", f)
            if m == None:
                continue
            rid = f[:-4]
            fd = codecs.open(os.path.join(directory, f), "r", "utf-8")
            cnstrnt = Constraint(fd, rid, self, self.mods, self.config)
            if cnstrnt.ok():
                # Store in the map, so that it is easy to access the
                # node by id.
                self.constraints[cnstrnt.get_id()] = cnstrnt
            else:
                self.error(87, "could not be parsed", cnstrnt.get_id())
                everythings_fine = False
        self.ts = time.time()
        return everythings_fine


    def deprecated_not_usable(self):
        self.state = self.er_error

    def deprecated_is_usable(self):
        return self.state == self.er_fine

    def deprecated_set_version_id(self, vid):
        self.version_id = vid

    def deprecated_own_write_analytics_result(self, mstderr):
        for k, v in sorted(self.analytics.items(),
                           key=operator.itemgetter(0)):
            if v[0] < 0:
                mstderr.write("+++ Error:Analytics:%s:result is '%+3d'\n"
                              % (k, v[0]))
                for l in v[1]:
                    mstderr.write("+++ Error:Analytics:%s:%s\n" % (k, l))

    # Write out the analytics results.
    def deprecated_write_analytics_result(self, mstderr):
        self.own_write_analytics_result(mstderr)
        for req in sorted(self.reqs.values(), key=lambda r: r.id):
            req.write_analytics_result(mstderr)

    def deprecated_normalize_dependencies(self):
        for r in self.reqs.itervalues():
            # Remove the old 'Depends on'
            r.record.remove("Depends on")

            # Create the list of dependencies
            onodes = []
            for n in r.incoming:
                onodes.append(n.name)

            # If the onodes is empty: There must no old 'Solved by'
            # tag available - if so something completey strange has
            # happens and it is better to stop directly.
            if len(onodes) == 0:
                assert(not r.record.is_tag_available("Solved by"))
                # Looks that everything is ok: continue
                continue

            onodes.sort()
            on = " ".join(onodes)

            # Check if there is already a 'Solved by'
            try:
                r.record.set_content("Solved by", on)
            except ValueError, ve:
                r.record.append(RecordEntry(
                        "Solved by", on,
                        "Added by rmtoo-normalize-dependencies"))
        return True

    def deprecated_write_to_filesystem(self, directory):
        for r in self.reqs.itervalues():
            fd = file(directory + "/" + r.id + ".req", "w")
            r.record.write_fd(fd)
            fd.close()
        return True

# Not used at the moment
#    def edge_count(self):
#        r = 0
#        for n in self.reqs.itervalues():
#            r += len(n.outgoing)
#        return r


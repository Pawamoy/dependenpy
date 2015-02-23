# -*- coding: utf-8 -*-

# Copyright (c) 2015 Timothée Mazzucotelli
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import sys
import ast
import json
import csv
import collections

def resolve_path(module):
    """Built-in method for getting a module's path within Python path.
    :param mod: *required* (string); the partial basename of the module
    :return: (string); the path to this module or None if not found
    """
    for path in sys.path:
        module_path = os.path.join(path, module.replace('.', '/'))
        if os.path.isdir(module_path):
            module_path += '/__init__.py'
            if os.path.exists(module_path):
                return module_path
            return None
        elif os.path.exists(module_path + '.py'):
            return module_path + '.py'
    return None


DEFAULT_OPTIONS = {
    'group_name': True,
    'group_index': True,
    'source_name': True,
    'source_index': True,
    'target_name': True,
    'target_index': True,
    'imports': True,
    'cardinal': True,
}


# TODO: Add exclude option
class DependencyMatrix:
    """A new instance of DependencyMatrix contains the list of packages you
    specified, optionally the associated groups, the options you passed, and
    attributes for the maximum depth of the modules, the list of these
    modules, and their imports (or dependencies). These last three attributes
    are initialized to 0 or an empty list. To compute them, use the build
    methods of the instance (build_modules, then build_imports).
    """

    def __init__(self, packages, path_resolver=resolve_path):
        """Instantiate a DependencyMatrix object.

        :param packages: string / list / OrderedDict containing packages to scan
        :param path_resolver: a callable that can find the absolute path given
        a module name
        """
        if isinstance(packages, str):
            self.packages = [[packages]]
            self.groups = ['']
        elif isinstance(packages, list):
            self.packages = [packages]
            self.groups = ['']
        elif isinstance(packages, collections.OrderedDict):
            self.packages = packages.values()
            self.groups = packages.keys()
        else:
            self.packages = packages
            self.groups = ['']
        self.path_resolver = path_resolver
        self.modules = []
        self.imports = []
        self.max_depth = 0
        self.matrices = []
        self._inside = {}
        self._modules_are_built = False
        self._imports_are_built = False
        self._matrices_are_built = False

    def build(self):
        """Shortcut for building modules, imports and matrices.
        """
        return self.build_modules().build_imports().build_matrices()

    def build_modules(self):
        """Build the module list with all python files in the given packages.
        Also compute the maximum depth.
        """
        if self._modules_are_built:
            return self
        group = 0
        for package_group in self.packages:
            for package in package_group:
                module_path = self.path_resolver(package)
                if not module_path:
                    continue
                module_path = os.path.dirname(module_path)
                self.modules.extend(
                    self._walk(package, module_path, group))
            group += 1
        self.max_depth = max([len(m['name'].split('.')) for m in self.modules])
        self._modules_are_built = True
        return self

    def build_imports(self):
        """Build the big dictionary of imports.
        """
        if not self._modules_are_built or self._imports_are_built:
            return self
        source_index = 0
        for module in self.modules:
            imports_dicts = self.parse_imports(module)
            for key in imports_dicts.keys():
                target_index = self.module_index(key)
                self.imports.append({
                    'source_name': module['name'],
                    'source_index': source_index,
                    'target_index': target_index,
                    'target_name': self.modules[target_index]['name'],
                    'imports': [imports_dicts[key]],
                    'cardinal': len(imports_dicts[key]['import'])
                })
            source_index += 1
        self._imports_are_built = True
        return self

    def build_matrices(self):
        """Build the matrices of each depth. Starts with the last one
        (maximum depth), and ascend through the levels
        until depth 1 is reached.
        """
        if not self._imports_are_built or self._matrices_are_built:
            return self
        md = self.max_depth
        self.matrices = [None for x in range(0, md)]
        self.matrices[md-1] = {'modules': self.modules,
                               'imports': self.imports}
        md -= 1
        while md > 0:
            self.matrices[md-1] = self._build_up_matrix(md)
            md -= 1
        self._matrices_are_built = True
        return self

    def module_index(self, module):
        """Return the index of the given module in the built list of modules.

        :param module: a string representing the module name (pack.mod.submod)
        """
        # We don't need to store results, since we have unique keys
        # See parse_imports -> sum_from

        # FIXME: what is the most efficient? 3 loops with 1 comparison
        # or 1 loop with 3 comparisons? In the second case: are we sure
        # we get an EXACT result (order of comparisons)?

        # Case 1: module is already a target
        idx = 0
        for m in self.modules:
            if module == m['name']:
                return idx
            idx += 1
        # Case 2: module is an __init__ target
        idx = 0
        for m in self.modules:
            if m['name'] == module+'.__init__':
                return idx
            idx += 1
        # Case 3: module is the sub-module of a target
        idx = 0
        for m in self.modules:
            if module.startswith(m['name']+'.'):
                return idx
            idx += 1
        # We should never reach this (see parse_imports -> if contains(mod))
        return None

    def contains(self, module):
        """Check if the specified module is part of the package list given
        to this object. Return True if yes, False if not.

        :param module: a string representing the module name (pack.mod.submod)
        """
        pre_computed = self._inside.get(module, None)
        if pre_computed is not None:
            return pre_computed
        else:
            for package_group in self.packages:
                for package in package_group:
                    if module == package or module.startswith(package+'.'):
                        self._inside[module] = True
                        return True
            self._inside[module] = False
            return False

    def parse_imports(self, module, force=False):
        """Return a dictionary of dictionaries with importing module (by)
        and imported modules (from and import). Keys are the importing modules.

        :param module: dict containing module's path and name
        :param force: bool, force append even if packages do not contain module
        :return: dict of dict
        """
        sum_from = collections.OrderedDict()
        code = open(module['path']).read()
        for node in ast.parse(code).body:
            if isinstance(node, ast.ImportFrom):
                if not node.module:
                    continue
                mod = node.module
                # We rebuild the module name if it is a relative import
                level = node.level
                if level > 0:
                    mod = os.path.splitext(module['name'])[0]
                    level -= 1
                    while level != 0:
                        mod = os.path.splitext(mod)[0]
                        level -= 1
                    mod += '.' + node.module
                if self.contains(mod) or force:
                    if sum_from.get(mod, None):
                        sum_from[mod]['import'] += [n.name for n in node.names]
                    else:
                        sum_from[mod] = {
                            'by': module['name'],
                            'from': mod,
                            'import': [n.name for n in node.names]}
        return sum_from

    def _build_up_matrix(self, down_level):
        """Build matrix data based on the matrix below it.

        :param down_level: int, depth of the matrix below
        """
        # First we build the new module list
        up_modules, up_imports = [], []
        seen_module, seen_import = {}, {}
        modules_indexes = {}
        index_old, index_new = 0, -1
        for m in self.matrices[down_level]['modules']:
            up_module = m['name'].split('.')[:down_level]
            # FIXME: We could maybe get rid of path...
            if seen_module.get(up_module, None) is not None:
                # seen_module[up_module]['path'] += ', ' + m['path']
                pass
            else:
                seen_module[up_module] = {
                    'name': up_module,
                    # 'path': m['path'],
                    'group_name': m['group_name'],
                    'group_index': m['group_index']
                }
                up_modules.append(seen_module[up_module])
                index_new += 1
            modules_indexes[index_old] = {'index': index_new,
                                          'name': up_module}
            index_old += 1

        # Then we build the new imports list
        for i in self.matrices[down_level]['imports']:
            new_source_index = modules_indexes[i['source_index']]['index']
            new_source_name = modules_indexes[i['source_index']]['name']
            new_target_index = modules_indexes[i['target_index']]['index']
            new_target_name = modules_indexes[i['target_index']]['name']
            seen_id = (new_source_index, new_target_index)
            if seen_import.get(seen_id, None) is not None:
                seen_import[seen_id]['cardinal'] += i['cardinal']
                seen_import[seen_id]['imports'] += i['imports']
            else:
                seen_import[seen_id] = {
                    'cardinal': i['cardinal'],
                    'imports': i['imports'],
                    'source_name': new_source_name,
                    'source_index': new_source_index,
                    'target_name': new_target_name,
                    'target_index': new_target_index,
                }
                up_imports.append(seen_import[seen_id])

        # We return the new dict of modules / imports
        return {'modules': up_modules, 'imports': up_imports}

    # TODO: Add exclude option
    def _walk(self, name, path, group, prefix=''):
        """Walk recursively into subdirectories of a directory and return a
        list of all Python files found (*.py).

        :param path: *required* (string); directory to scan
        :param prefix: *optional* (string); file paths prepended string
        :return: (list); the list of Python files
        """
        result = []
        for item in os.listdir(path):
            sub_item = os.path.join(path, item)
            if os.path.isdir(sub_item):
                result.extend(self._walk(
                    name, sub_item, group,
                    '%s%s/' % (prefix, os.path.basename(sub_item))))
            elif item.endswith('.py'):
                result.append({
                    'name': '%s.%s' % (
                        name, os.path.splitext(
                            prefix+item)[0].replace('/', '.')),
                    'path': sub_item,
                    'group_index': group,
                    'group_name': self.groups[group]
                })
        return result

    def to_json(self):
        """Return self as a JSON string (without path_resolver callable).
        """
        return json.dumps({
            'packages': self.packages,
            'groups': self.groups,
            # 'path_resolver': self.path_resolver,
            'modules': self.modules,
            'imports': self.imports,
            'max_depth': self.max_depth,
            'matrices': self.matrices,
            '_inside': self._inside,
            '_modules_are_built': self._modules_are_built,
            '_imports_are_built': self._imports_are_built,
            '_matrices_are_built': self._matrices_are_built,
        })

    @staticmethod
    def _option_filter(matrix, options):
        """Return a light version of a matrix based on given options.

        :param matrix: a matrix from self.matrices
        :param options: dict of booleans. keys are group_name, group_index,
        source_name, source_index, target_name, target_index, imports, cardinal
        """
        if not options['group_name']:
            for item in matrix['modules']:
                del item['group_name']
        if not options['group_index']:
            for item in matrix['modules']:
                del item['group_index']
        if not options['source_name']:
            for item in matrix['imports']:
                del item['source_name']
        if not options['source_index']:
            for item in matrix['imports']:
                del item['source_index']
        if not options['target_name']:
            for item in matrix['imports']:
                del item['target_name']
        if not options['target_index']:
            for item in matrix['imports']:
                del item['target_index']
        if not options['imports']:
            for item in matrix['imports']:
                del item['imports']
        if not options['cardinal']:
            for item in matrix['imports']:
                del item['cardinal']
        return matrix

    def get_matrix(self, matrix):
        """Return a copy of the specified matrix.
        Cast given index into [0 .. max_depth] range.

        :param matrix: index/depth. Zero return max_depth matrix.
        """
        if matrix == 0 or matrix > self.max_depth:
            m = self.max_depth-1
        elif matrix < 0:
            m = 0
        else:
            m = matrix-1
        return dict(self.matrices[m])

    def matrix_to_json(self, matrix, options=DEFAULT_OPTIONS):
        """Return a matrix from self.matrices as a JSON string.

        :param matrix: index/depth of matrix (begin to 1, end to max_depth,
        and 0 is equivalent to max_depth)
        :param options: dict of filter options
        """
        return json.dumps(
            DependencyMatrix._option_filter(
                self.get_matrix(matrix), options))
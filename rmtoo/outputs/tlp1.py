#
# rmtoo
#   Free and Open Source Requirements Management Tool
#
# LaTeX output class version 2
#
# (c) 2011 by flonatel
#
# For licencing details see COPYING
#

import time

class tlp1:

    class Id2IntMapper:

        def __init__(self):
            self.next_int = 0
            self.mappings = {}
            self.imapping = {}

        def get(self, n):
            if n in self.mappings:
                return self.mappings[n]
            oi = self.next_int
            self.mappings[n] = oi
            self.imapping[oi] = n
            self.next_int += 1
            return oi

    def __init__(self, params):
        self.topic_name = params[0]
        self.filename = params[1]

    def set_topics(self, topics):
        self.topic_set = topics.get(self.topic_name)

    # Create Makefile Dependencies
    def cmad(self, reqscont, ofile):
        ofile.write("%s: ${REQS}\n\t${CALL_RMTOO}\n" % (self.filename))

    # The real output
    # Note that currently the 'reqscont' is not used in case of topics
    # based output.
    def output(self, reqscont):
        # Currently just pass this to the RequirementSet
        self.output_reqset(reqscont.continuum_latest())

    def output_reqset(self, reqset):
        fd = file(self.filename, "w")
        reqs_count = reqset.reqs_count()
        i2im = tlp1.Id2IntMapper()
        self.write_header(fd)
        self.write_node_ids(fd, reqs_count)
        self.write_edges(fd, reqset, i2im)
        self.write_labels(fd, i2im)
        self.write_footer(fd)
        fd.close()
        
    # Details
    def write_header(self, fd):
        fd.write('(tlp "2.0"\n')
        # ToDO: very complicated to check this during tests.
        #fd.write('(date "%s")\n' % time.strftime("%d-%m-%Y")) 
        fd.write('(comments "This file was generated by rmtoo.")\n')

    def write_node_ids(self, fd, m):
        fd.write("(nodes ")
        for i in xrange(0, m):
            fd.write("%d " % i)
        fd.write(")\n")

    def write_edges(self, fd, reqset, i2im):
        e = 0
        for r in sorted(reqset.reqs.itervalues(), key=lambda r: r.id):
            ei = i2im.get(r.id)
            for o in sorted(r.outgoing, key = lambda t: t.name):
                ej = i2im.get(o.id)
                fd.write("(edge %d %d %d)\n" % (e, ei, ej))
                e += 1

    def write_labels(self, fd, i2im):
        fd.write('(property  0 string "viewLabel"\n')
        fd.write('(default "" "" )')

        for k,v in sorted(i2im.imapping.items()):
            fd.write('(node %d "%s")\n' % (k, v))
        fd.write(")\n")

    def write_footer(self, fd):
        fd.write(")\n")

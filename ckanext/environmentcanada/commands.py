from ckan.lib.cli import CkanCommand
from ckanext.canada.metadata_schema import schema_description
from fnmatch import fnmatch
from HTMLParser import HTMLParser
from lxml import etree
from os import listdir
from os import path
from paste.script import command
import json
import logging
import re
import sys
import traceback

class ECCommand(CkanCommand):
    """CKAN Environment Canada Extension

    Usage:
        paster environmentcanada print_one -s <source-file> [-f <file-name>]
                                 import_dir -d <directory> [-e <file-ext>] [-f <file-name>]

    Arguments:
        <file-name>   is the name of a text file to write out the updated records in JSON Lines format
        <report_file> is the name of a text to write out a import records report in .csv format
        <source-file> is the file path to a single EC NAP file to parser
        <file-ext>    is a file extension to use, normally this is 'xml'.

    Options:
        -h Display help message
        -c/--config <configuration file>    Paster configuration file
        -d/--dir <directory>                A directory that contains NAP files
        -e/--file-ext <file-ext>            NAP file extension to look for, normally this is 'xml'.
        -s/--source_file <source-file>      Single EC Nap File

    """
    summary = __doc__.split('\n')[0]
    usage = __doc__

    parser = command.Command.standard_parser(verbose=True)
    parser.add_option('-s', '--source-file', dest='source', help='EC NAP file to parse')
    parser.add_option('-c', '--config', dest='config',
        default='development.ini', help='Configuration file to use.')
    parser.add_option('-d', '--dir', dest='dir', help='Directory that contains files to parse')
    parser.add_option('-e', '--file-ext', dest='ext', default='xml', help='File extension to use')
    parser.add_option('-f', '--json-file', dest='jl_file', help='Write EC records in JSON lines format to this file')

    def command(self):
        '''
        Parse command line arguments and call appropriate method.
        '''
        if not self.args or self.args[0] in ['--help', '-h', 'help']:
            print self.__doc__
            return

        cmd = self.args[0]

        self._load_config()

        self.logger = logging.getLogger('ckanext')

        self.nap_namespaces = {'gmd'   : 'http://www.isotc211.org/2005/gmd',
                               'gco'   : 'http://www.isotc211.org/2005/gco',
                               'xsi'   : 'http://www.w3.org/2001/XMLSchema-instance',
                               'gml'   : 'http://www.opengis.net/gml',
                               'xlink' : 'http://www.w3.org/1999/xlink'}

        self.ds_update_freq_map = {
            'asNeeded'    : "As Needed | Au besoin",
            'continual'   : "Continual | Continue",
            'daily'       : "Daily | Quotidien",
            'weekly'      : "Weekly | Hebdomadaire",
            'fortnightly' : "Fortnightly | Quinzomadaire",
            'monthly'     : "Monthly | Mensuel",
            'semimonthly' : "Semimonthly | Bimensuel",
            'quarterly'   : "Quarterly | Trimestriel",
            'biannually'  : "Biannually | Semestriel",
            'annually'    : "Annually | Annuel",
            'irregular'   : u"Irregular | Irr\u00e9gulier",
            'notPlanned'  : u"Not Planned | Non planifi\u00e9",
            'unknown'     : "Unknown | Inconnu"}

        self.reasons = ""

        self.output_file = sys.stdout
        self.display_formatted = True

        # Default output is JSON lines (one JSON record per line) but human-readable formatting is an option
        if self.options.jl_file:
            self.output_file = open(path.normpath(self.options.jl_file), 'wt')
            self.display_formatted = False

        # Topic categories
        self.topic_choices = dict((c['eng'], c)
            for c in schema_description.dataset_field_by_id['topic_category']['choices'] if 'eng' in c)

        if cmd == 'print_one':
            print  >>  self.output_file, json.dumps(self._to_od_dataset(self.options.source), indent = 2 * ' ')

        # Import all the files from a directory with the provided file extension, and print them to the JSON output file.
        elif cmd == 'import_dir':
            if self.options.dir:
                files = [name for name in listdir(self.options.dir)
                         if fnmatch(name, '*.%s' % self.options.ext)]
                if len(files) == 0:
                    print "No matching files found"
                    return
                else:
                    for source_file in files:
                        print "Importing File: %s" % source_file
                        json_line = self._to_od_dataset(path.join(self.options.dir, source_file))
                        if json_line != None:
                            if self.display_formatted:
                                print  >>  self.output_file,  (json.dumps(json_line, indent = 2 * ' '))
                            else:
                                print  >>  self.output_file,  (json.dumps(json_line, encoding="utf-8"))
                        else:
                            print self.reasons

                print ""
            else:
                print "Missing arguement"
                print self.__doc__
                return

    def _to_od_dataset(self, source_file):
        """Convert a NAP file into an Open Data record"""
        odproduct = {}
        valid = True
        self.reasons = ""

        try:

            self.root = etree.parse(source_file)

            # Boilerplate fields for the Open Data record

            odproduct['author_email'] = "open-ouvert@tbs-sct.gc.ca"
            odproduct['language'] = "eng; CAN | fra; CAN"
            odproduct['owner_org'] = "ec"
            odproduct['department_number'] = "99"
            odproduct['catalog_type'] = u"Geo Data | G\u00e9o"
            odproduct['license_id'] = u"ca-ogl-lgo"
            odproduct['attribution'] = u"Contains information licensed under the Open Government Licence \u2013 Canada."
            odproduct['attribution_fra'] = u"Contient des informations autoris\u00e9es sous la Licence du gouvernement ouvert- Canada"
            odproduct['ready_to_publish'] = True
            odproduct['portal_release_date'] = ""
            odproduct['presentation_form'] = u"Document Digital | Document num\u00e9rique"
            odproduct['spatial_representation_type'] = "Vector | Vecteur"

            # Read in NAP fields and populate the OD dataset

            # UUID identifier

            odproduct['id'] = self._get_first_text('/gmd:MD_Metadata/gmd:fileIdentifier/gco:CharacterString')

            # Title - English and French

            odproduct['title'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:citation/gmd:CI_Citation/gmd:title/gco:CharacterString')
            if len(odproduct['title']) == 0:
                self.reasons = '%s No English Title Given;' % self.reasons
                valid = False

            odproduct['title_fra'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:citation/gmd:CI_Citation/gmd:title/gmd:PT_FreeText/gmd:textGroup/gmd:LocalisedCharacterString')
            if len(odproduct['title_fra']) == 0:
                self.reasons = '%s No French Title Given;' % self.reasons
                valid = False

            # Description - English and French

            odproduct['notes'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:abstract/gco:CharacterString').replace(u"\u2019", "'")

            odproduct['notes_fra'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:abstract/gmd:PT_FreeText/gmd:textGroup/gmd:LocalisedCharacterString').replace(u"\u2019", "'")

            # Time Period Coverage - Start and End (optional)

            coverage_start_time = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:temporalElement/gmd:EX_TemporalExtent/gmd:extent/gml:TimePeriod/gml:beginPosition')
            if not coverage_start_time is None:
                if len(coverage_start_time) == 4:
                    coverage_start_time = "%s-01-01" % coverage_start_time
                odproduct['time_period_coverage_start'] = coverage_start_time

            coverage_end_time = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:temporalElement/gmd:EX_TemporalExtent/gmd:extent/gml:TimePeriod/gml:endPosition').strip()
            # The time period coverage end time is not always present - it's not mandatory
            if (coverage_end_time.lower() <> u"ongoing") and (not len(coverage_end_time) == 0):
                if len(coverage_end_time) == 4:
                    coverage_end_time = "%s-12-31" % coverage_end_time
                odproduct['time_period_coverage_end'] = coverage_end_time

            # Homepage and Endpoint URLs - English and French

            sup_text = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:supplementalInformation/gco:CharacterString')
            urls_en = []
            if len(sup_text) > 0:
                urls_en = self._get_urls_from_string(sup_text)

            sup_text = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:supplementalInformation/gmd:PT_FreeText/gmd:textGroup/gmd:LocalisedCharacterString')
            urls_fr = []
            if len(sup_text) > 0:
                urls_fr = self._get_urls_from_string(sup_text)

            if len(urls_en) > 0:
                odproduct['url'] = urls_en[0]
            if len(urls_fr) > 0:
                odproduct['url_fra'] = urls_fr[0]

            if len(urls_en) > 1:
                odproduct['endpoint_url'] = urls_en[1]
            if len(urls_fr) > 1:
                odproduct['endpoint_url_fra'] = urls_fr[1]

            # GoC Subject

            topics_subjects = self._get_gc_subject_category(self.root.xpath('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:topicCategory/gmd:MD_TopicCategoryCode',
                                                                            namespaces=self.nap_namespaces))

            odproduct['subject'] = topics_subjects['subjects']
            if len(odproduct['subject']) == 0:
                valid = False
                self.reasons = '%s No GC Subjects;' % self.reasons

            # GoC Topic

            odproduct['topic_category'] = topics_subjects['topics']
            if len(odproduct['topic_category']) == 0:
                valid = False
                self.reasons = '%s No GC Topics;' % self.reasons

            # Tags - English and French

            odproduct['keywords'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:descriptiveKeywords/gmd:MD_Keywords/gmd:keyword/gco:CharacterString')
            if len(odproduct['keywords']) == 0:
                valid = False
                self.reasons = '%s No English Keywords;' % self.reasons

            odproduct['keywords_fra'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:descriptiveKeywords/gmd:MD_Keywords/gmd:keyword/gmd:PT_FreeText/gmd:textGroup/gmd:LocalisedCharacterString')
            odproduct['keywords_fra'] = odproduct['keywords_fra'].replace(u"/u2019", "'")
            if len(odproduct['keywords_fra']) == 0:
                valid = False
                self.reasons = '%s No French Keywords;' % self.reasons

            # Spatial - Convert a bounding box into a GeoJSON polygon

            westLong = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:geographicElement/gmd:EX_GeographicBoundingBox/gmd:westBoundLongitude/gco:Decimal')

            eastLong = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:geographicElement/gmd:EX_GeographicBoundingBox/gmd:eastBoundLongitude/gco:Decimal')

            northLat = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:geographicElement/gmd:EX_GeographicBoundingBox/gmd:northBoundLatitude/gco:Decimal')

            southLat = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:extent/gmd:EX_Extent/gmd:geographicElement/gmd:EX_GeographicBoundingBox/gmd:southBoundLatitude/gco:Decimal')

            # convert these 4 points into a bounding box
            odproduct['spatial'] = '{\"type\": \"Polygon\", \"coordinates\": [[[%s, %s], [%s, %s], [%s, %s], [%s, %s], [%s, %s]]]}' % (
                                   westLong,northLat,eastLong,northLat,eastLong,southLat,westLong,southLat,westLong,northLat)

            # Data Published

            odproduct['date_published'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:citation/gmd:CI_Citation/gmd:date/gmd:CI_Date/gmd:date/gco:Date')

            # Browse Graphic File Name

            try:
                odproduct['browse_graphic_url'] = self._get_first_text('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:graphicOverview/gmd:MD_BrowseGraphic/gmd:fileName/gco:CharacterString')
                if len(odproduct['browse_graphic_url']) == 0:
                    odproduct['browse_graphic_url'] = '/static/img/canada_default.png'
            except:
                odproduct['browse_graphic_url'] = '/static/img/canada_default.png'

            # Frequency

            odproduct['maintenance_and_update_frequency'] = self._get_update_frequency(
                self.root.xpath('/gmd:MD_Metadata/gmd:identificationInfo/gmd:MD_DataIdentification/gmd:resourceMaintenance/gmd:MD_MaintenanceInformation/gmd:maintenanceAndUpdateFrequency/gmd:MD_MaintenanceFrequencyCode/@codeListValue',
                           namespaces=self.nap_namespaces)[0])

            # Data Series Name, Issue Identification, DOI; These fields are not present in the EC ISO 19115 NAP files.

            odproduct['data_series_name'] = ''
            odproduct['data_series_name_fra'] = ''
            odproduct['data_series_issue_identification'] = ''
            odproduct['data_series_issue_identification_fra'] = ''
            odproduct['digital_object_identifier'] = ""

            # Load the Resources

            resources = self.root.xpath('/gmd:MD_Metadata/gmd:distributionInfo/gmd:MD_Distribution/gmd:transferOptions/gmd:MD_DigitalTransferOptions/gmd:onLine',
                                  namespaces=self.nap_namespaces)
            od_resources = []
            for resource in resources:
                od_resource = {}
                lang_code = resource.xpath('@xlink:role', namespaces=self.nap_namespaces)[0]
                if lang_code == "urn:xml:lang:eng-CAN":
                    od_resource['language'] = 'eng; CAN'
                elif lang_code == "urn:xml:lang:fra-CAN":
                    od_resource['language'] = 'fra; CAN'
                else:
                    od_resource['language'] = 'zxx; CAN'
                if len(resource.xpath('gmd:CI_OnlineResource/gmd:name/gco:CharacterString',
                                                     namespaces=self.nap_namespaces)) > 0:
                    od_resource['name'] = resource.xpath('gmd:CI_OnlineResource/gmd:name/gco:CharacterString',
                                                         namespaces=self.nap_namespaces)[0].text
                else:
                    if lang_code == "urn:xml:lang:eng-CAN":
                        od_resource['name'] = "Dataset"
                    else:
                        od_resource['name'] = u"Donn\u00e9es"
                od_resource['name_fra'] = od_resource['name']
                od_resource['resource_type'] = "file"
                od_resource['url'] = resource.xpath('gmd:CI_OnlineResource/gmd:linkage/gmd:URL', namespaces=self.nap_namespaces)[0].text
                od_resource['size'] = ''
                od_resource['format'] = self._guess_resource_type(od_resource['name'])
                if not od_resource['format'] == 'none':
                  od_resources.append(od_resource)
            odproduct['resources'] = od_resources


        except Exception as e:
            print("Failure: ", e)
            traceback.print_exc()

        if valid:
            return odproduct
        else:
            return None


    def _get_first_text(self, xpath_query):
        """When there is only one tag with one text field, retrieve the first tag and replace right apostrophes"""
        try:
            text_value = ""
            tag_list = self.root.xpath(xpath_query, namespaces=self.nap_namespaces)
            if len(tag_list) == 0:
                return text_value
            first_tag = tag_list[0]
            if first_tag.text:
                text_value = first_tag.text.replace(u"\u2019", "'")
                text_value = text_value.replace("(", " ")
                text_value = text_value.replace(")", " ")
            return text_value
        except Exception as e:
            print ("Error ", e, xpath_query)
            raise


    def _get_urls_from_string(self, text_with_urls):
        """Return a list of URLs that are embedded with a long text string."""
        unescaped_urls = []
        if len(text_with_urls) > 0:
            urls = re.findall('http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text_with_urls)
            html_parser =  HTMLParser()
            for url in urls:
                unescaped_urls.append(html_parser.unescape(url))
        return unescaped_urls


    def _get_gc_subject_category(self, geocategories):
        """Look up the GoC thesaurus values to determine topics and subjects.

        The Open Data schema uses the Government of Canada (GoC) thesaurus to enumerate valid topics and subjects.
        The schema provides a mapping of subjects to topic categories. Geogratis records provide GoC topics.
        This function looks up the subjects for these topics and returns two dictionaries with appropriate
        Open Data topics and subjects for this Geogratis record.

        """
        topics = []
        subjects = []

        schema_categories = schema_description.dataset_field_by_id['topic_category']['choices']

        topic_categories = []
        for geocat in geocategories:
            if not geocat.text is None:
                topic_categories.append(geocat.text.title())

        # Subjects are mapped to the topics in the schema, so both are looked up from the topic keys
        for topic in topic_categories:
            # This seems to be a common pattern - deal with it
            if topic == 'Climatologymeteorologyatmosphere':
                topic_key = 'Climatology / Meteorology / Atmosphere'
            elif topic == 'Geoscientificinformation':
                topic_key = 'Geoscientific Information'
            else:
                topic_key = re.sub("([a-z])([A-Z])","\g<1> \g<2>", topic).title()
            if not topic_key in self.topic_choices.keys():
                continue
            topics.append(self.topic_choices[topic_key]['key'])
            topic_subject_keys = self.topic_choices[topic_key]['subject_ids']
            for topic_subject_key in topic_subject_keys:
                if schema_description.dataset_field_by_id['subject']['choices_by_id'][topic_subject_key]:
                    subjects.append(schema_description.dataset_field_by_id['subject']['choices_by_id'][topic_subject_key]['key'])

        return { 'topics' : topics, 'subjects' : subjects}

    def _get_update_frequency(self, rawFrequency):
        """Map the EC update frequency key to the Open Data value, or return 'unknown'"""
        if rawFrequency == "":
            return self.ds_update_freq_map['unknown']
        if self.ds_update_freq_map[rawFrequency]:
            return self.ds_update_freq_map[rawFrequency]
        else:
            return self.ds_update_freq_map['unknown']

    def _guess_resource_type(self, title):
        """Try to determine the file type of the resource from the file name"""
        if title is None:
          return "none"
        if len(re.findall('csv', title, flags=re.IGNORECASE)) > 0:
            return "CSV"
        elif len(re.findall('html', title, flags=re.IGNORECASE)) > 0:
            return "HTML"
        else:
            return "other"



import os
import requests
from datetime import datetime
import time
import re
import csv
import pytz
import dateutil.parser
from simple_salesforce import Salesforce, SFType
from slackclient import SlackClient
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
import pdb

eastern = pytz.timezone('US/Eastern')

# load settings from .env file
load_dotenv()

# constants
if os.environ.get("SALESFORCE_URL") is not None:
    SALESFORCE_URL = os.environ.get("SALESFORCE_URL")
    FLOAT_API_KEY = os.environ.get("FLOAT_API_KEY")
else:
    SALESFORCE_URL = "SALESFORCE DOMAIN"

RTM_READ_DELAY = 1 # 1 second delay between reading from RTM
EXAMPLE_COMMAND = "sync"
EXAMPLE_COMMANDs = ["sync", "report"]
MENTION_REGEX = "^<@(|[WU].+?)>(.*)"

def get_start_end_dates(year, week):
    d = date(year,1,1)
    if(d.weekday()<= 3):
        d = d - timedelta(d.weekday())             
    else:
        d = d + timedelta(7-d.weekday())
    dlt = timedelta(days = (week-1)*7)
    return {
        "start_datetime": d + dlt, 
        "end_datetime": d + dlt + timedelta(days=6)
    }

class FloatAPI:
    """
        api wrapper for FLOAT.COM
    """
    def __init__(self):
        self.url = "https://api.float.com/v3"
        self.access_key = FLOAT_API_KEY             # access key to float.com
        self.projects = []
        self.tasks = []
        self.people = []
        self.base_headers = {
            "Authorization":"Bearer {}".format(self.access_key),
            "User-Agent":"MedmetryPyFloat",
            "Content-Type":"application/json",
            "Accept":"application/json"
        }

    def get_people(self):
        resp = requests.get(self.url+"/people", headers=self.base_headers)

        if resp.status_code == 200:
            self.people = resp.json()
            return resp.json()
        else:
            return None

    def get_person_by_id(self, people_id=17145442):
        resp = requests.get("{}/people/{}".format(self.url, people_id),
                            headers=self.base_headers)

        if resp.status_code < 400:
            return resp.json()
        else:
            return None

    def get_projects(self):
        headers = self.base_headers
        headers["X-Pagination-Current-Page"] = '2'
        resp = requests.get(self.url+"/projects?per-page=200", headers=headers)

        if resp.status_code == 200:
            self.projects = resp.json()
            return self.projects
        else:
            return None

    def get_project_by_id(self, project_id=17145442):
        resp = requests.get("{}/projects/{}".format(self.url, project_id),
                            headers=self.base_headers)

        if resp.status_code < 400:
            return resp.json()
        else:
            return None

    def get_tasks(self):
        resp = requests.get(self.url+"/tasks", headers=self.base_headers)

        if resp.status_code == 200:
            self.tasks = resp.json()
            return self.tasks
        else:
            return None

    def get_task_by_id(self, task_id=17145442):
        resp = requests.get("{}/tasks/{}".format(self.url, task_id),
                            headers=self.base_headers)

        if resp.status_code < 400:
            return resp.json()
        else:
            return None

    def get_tasks_by_params(self, params):
        """
            Get tasks by parameters
            /tasks/?project_id=xxxxxx
        """
        print("{}/tasks?{}".format(self.url, params))
        resp = requests.get("{}/tasks?{}".format(self.url, params),headers=self.base_headers)

        if resp.status_code < 400:
            return resp.json()
        else:
            return []

    def test(self):
        float_tasks = []

        projects = self.get_projects()
        for project in projects:
            if 'Southern Indiana' in project["name"]:
                float_tasks = self.get_tasks_by_params('project_id={}'.format(project["project_id"]))

        return float_tasks


class ScheduleBot:
    """
        slackbot class
    """
    def __init__(self):
        # instantiate Salesforce instance
        self.sf = None
        self.project_table_name = 'pse__Proj__c'

        # instantiate Slack client
        self.slack_client = SlackClient(os.environ.get("SLACK_BOT_TOKEN"))
        # bot's user ID in Slack: value is assigned after the bot starts up
        self.slack_client_id = None
        # variable to count updated tasks
        self.number_of_success = 0

    def create_salesforce_instance(self, session_id):
        self.sf = Salesforce(instance=SALESFORCE_URL, session_id=session_id)

    def set_project_table_name(self):
        sobjects = self.sf.query_more("/services/data/v37.0/sobjects/", True)
        for sobject in sobjects["sobjects"]:
            if sobject["labelPlural"] == "Projects":
                self.project_table_name = sobject.name


    def parse_bot_commands(self, slack_events):
        """
            Parses a list of events coming from the Slack RTM API to find bot commands.
            If a bot command is found, this function returns a tuple of command and channel.
            If its not found, then this function returns None, None.
        """
        for event in slack_events:
            if event["type"] == "message" and not "subtype" in event:
                user_id, message = self.parse_direct_mention(event["text"])
                if user_id == self.slack_client_id:
                    return message, event["channel"]
        return None, None

    def parse_direct_mention(self, message_text):
        """
            Finds a direct mention (a mention that is at the beginning) in message text
            and returns the user ID which was mentioned. If there is no direct mention, returns None
        """
        matches = re.search(MENTION_REGEX, message_text)
        # the first group contains the username, the second group contains the remaining message
        return (matches.group(1), matches.group(2).strip()) if matches else (None, None)

    def handle_command(self, command, channel):
        """
            Executes bot command if the command is known
        """
        # Default response is help text for the user
        default_response = "Not sure what you mean. Try *{}*.".format("sync or report")

        # Finds and executes the given command, filling in response
        response = None

        is_command_syntax_correct = False
        for example_command in EXAMPLE_COMMANDs:
            if command.startswith(example_command):
                is_command_syntax_correct = True

        if is_command_syntax_correct == False:
            self.slack_client.api_call(
                "chat.postMessage",
                channel=channel,
                text=default_response
            )
        else:
            # This is where you start to implement more commands!
            command_args = command.split(" ")
            if command_args[0] == u'report':
                self.get_tasks_by_weeks(channel)
                self.slack_client.api_call(
                    "chat.postMessage",
                    channel=channel,
                    text=response or 'Upload Finished!'
                )
            else:
                session_id = command_args[1]
                self.create_salesforce_instance(session_id)
                self.sync_tasks(channel)

    def run(self):
        if self.slack_client.rtm_connect(with_team_state=False):
            print("Starter Bot connected and running!")
            # Read bot's user ID by calling Web API method `auth.test`
            self.slack_client_id = self.slack_client.api_call("auth.test")["user_id"]
            while True:
                try:
                    command, channel = self.parse_bot_commands(self.slack_client.rtm_read())
                    if command:
                        self.handle_command(command, channel)
                    time.sleep(RTM_READ_DELAY)
                except:
                    self.slack_client.rtm_connect(with_team_state=False)
                    self.slack_client_id = self.slack_client.api_call("auth.test")["user_id"]
        else:
            print("Connection failed. Exception traceback printed above.")

    def sync_tasks(self, channel):
        self.slack_client.api_call(
            "chat.postMessage",
            channel=channel,
            text='Please wait a moment...'
        )

        is_session_valid = True
        try:
            self.sf.query_more("/services/data/v37.0/sobjects/", True)
        except:
            is_session_valid = False

        if is_session_valid:
            try:
                sf_tasks = []       
                sf_project_task = SFType('pse__Project_Task__c', session_id, SALESFORCE_URL)
                float_api = FloatAPI()

                projects = float_api.get_projects()
                for project in projects:
                    m = re.search(r'(?<=-)\d+', project["name"])
                    if m is not None:
                        sf_project_id = m.group(0)
                        # float_tasks = float_api.test()
                        tmp_float_tasks = float_api.get_tasks_by_params(
                                            'project_id={}'.format(project["project_id"])
                                        )

                        float_tasks = []
                        float_task_hash = {}
                        for tmp_task in tmp_float_tasks:
                            tmp_user = float_api.get_person_by_id(tmp_task["people_id"])
                            task_name = tmp_task["name"]
                            if task_name not in float_task_hash:
                                tmp_task["users"] = self.format_username(tmp_user["name"])
                                float_task_hash[task_name] = tmp_task
                                float_tasks.append(tmp_task)
                            else:
                                first_start_date =  datetime.strptime(
                                    float_task_hash[task_name]["start_date"],
                                    '%Y-%m-%d'
                                ).strftime("%V")
                                second_start_date = datetime.strptime(
                                    tmp_task["start_date"], '%Y-%m-%d'
                                    ).strftime("%V")

                                if first_start_date == second_start_date:
                                    float_task_hash[task_name]["users"] = self.format_username(float_task_hash[task_name]["users"]) + ', ' + self.format_username(tmp_user["name"])
                                else:
                                    tmp_task["is_duplicaate"] = True
                                    float_task_hash[task_name] = tmp_task
                                    float_tasks.append(tmp_task)

                        if len(float_tasks) > 0:
                            # tags = float_api.get_project_by_id(float_tasks[0]["project_id"])["tags"]
                            sf_tasks = bot.test('PR-'+sf_project_id)                                      

                            for float_task_key in float_task_hash.keys():
                                # fl_user = float_api.get_person_by_id(float_task["people_id"])
                                float_task = float_task_hash[float_task_key]
                                if 'is_duplicate' in float_task:
                                    self.slack_client.api_call(
                                        "chat.postMessage",
                                        channel=channel,
                                        text='Project has two tasks. Please manually sync the second in Salesforce, or use a different task name'
                                    )
                                else:
                                    for sf_task in sf_tasks:
                                        # if float_task["name"] == 'Go Live' and  sf_task["Name"] == 'Onsite Go Live':
                                        if float_task["name"] == sf_task["Name"]:
                                            start_datetime = datetime.strptime(float_task['start_date'], '%Y-%m-%d')
                                            end_datetime = datetime.strptime(float_task['end_date'], '%Y-%m-%d')

                                            start_datetime_obj = eastern.localize(start_datetime).strftime("%Y-%m-%dT%H:%M:%S")
                                            end_datetime_obj = eastern.localize(end_datetime).strftime("%Y-%m-%dT%H:%M:%S")

                                            msg = ''
                                            params = {}
                                            if sf_task['pse__Assigned_Resources__c'] != float_task["users"]:
                                                params["pse__Assigned_Resources__c"] = float_task["users"]
                                                params["pse__Assigned_Resources_Long__c"] = float_task["users"]
                                                msg = 'assigned resources '

                                            if self.remove_delta(sf_task['pse__Start_Date_Time__c']) != start_datetime_obj.decode() or self.remove_delta(sf_task['pse__End_Date_Time__c']) != end_datetime_obj.decode():
                                                params['pse__Start_Date_Time__c'] = start_datetime_obj
                                                params['pse__End_Date_Time__c'] = end_datetime_obj
                                                msg = 'start & end time '

                                            if len(params.keys()) > 0:
                                                result = sf_project_task.update(sf_task["Id"], params, False)

                                                task_status_response = ''
                                                if result < 400:
                                                    self.number_of_success = self.number_of_success + 1
                                                    task_status_response = "{} | {} | project {}".format(
                                                        msg,
                                                        float_task["name"],
                                                        project["name"])
                                                    self.slack_client.api_call(
                                                        "chat.postMessage",
                                                        channel=channel,
                                                        text=task_status_response
                                                    )

            except Exception as e:
                pass
        else:
            response = 'Session is incorrect or expired!'

        # Sends the response back to the channel
        self.slack_client.api_call(
            "chat.postMessage",
            channel=channel,
            text=response or 'Finished!'
        )
    
    def get_detail_task(self, task_url):
        sobject = self.sf.query_more(task_url, True)
        return sobject


    def get_milestone_id(self, project_id, milestone_name='Implementation and Training'):
        query = "select Id, Name from pse__Proj__c where pse__Project_ID__c='{}'".format(project_id)

        sobject = self.sf.query(query)

        if sobject["totalSize"] == 0:
            return None

        global_project_id = sobject["records"][0]['Id']

        query = "select Id, Name from pse__Milestone__c \
                where pse__Project__c='{}' and Name='{}'".format(global_project_id, milestone_name)
        
        sobject = self.sf.query(query)
        if sobject["totalSize"] == 0:
            return None

        return {
            'milestone_id': sobject["records"][0]['Id'],
            'project_id': global_project_id
        }


    def get_task_by_milestone_and_product(self, project_id, milestone_id):
        query = "select Id, pse__Project__c from pse__Project_Task__c \
                where (pse__Project__c='{}' and pse__Milestone__c='{}')".format(project_id, milestone_id)

        sobject = self.sf.query(query)
        if sobject["totalSize"] == 0:
            return []

        return sobject["records"]

    def format_time(self, time_val):
        if time_val is None:
            return time_val
        return time_val.replace(' ', 'T')

    def remove_delta(self, time_val):
        if time_val is None:
            return time_val
        return time_val.replace('+0000', '').replace('.000', '')

    def format_username(self, val):
        if val is None:
            return None
        return val.split("-")[0].strip()

    def upload(self, file, channel):
        try:
            with open(file) as file_content:
                res = self.slack_client.api_call(
                        "files.upload",
                        channels=channel,
                        file=file_content,
                        title="Test upload"
                    )

                file_content.close()
        except Exception as e:
            self.slack_client.api_call(
                "chat.postMessage",
                channel=channel,
                text=e.message
            )

        return True

    def get_tasks_by_weeks(self, channel):
        float_api = FloatAPI()
        report_schedules = []
        fieldnames = [
            "start_date",
            "on_vocation",
            "in_training_for_teaching",
            "in_training_for_learning",
            "onsite_go_live",
            "onsite_setup",
            "remote_training"
        ]

        # create csv file with header
        with open('report.csv', 'w') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()

        year = datetime.now().strftime("%Y")
        month = datetime.now().strftime("%m")
        next_month = datetime.now() + dateutil.relativedelta.relativedelta(months=2)
        before_month = datetime.now() - dateutil.relativedelta.relativedelta(months=1)

        start_next_month = datetime.strptime(
            '{}-{}-1'.format(year, next_month.strftime("%m")),
            '%Y-%m-%d'
        )
        start_date_of_month = datetime.strptime(
            '{}-{}-1'.format(year, before_month.strftime("%m")),
            '%Y-%m-%d'
        )

        last_date_of_month = start_next_month  - timedelta(days=1)
        start_weeknum = start_date_of_month.strftime("%V")
        end_weeknum = last_date_of_month.strftime("%V")

        for week_num in range(int(start_weeknum), int(end_weeknum)):
            date_obj = get_start_end_dates(2019, week_num)
            start_date = date_obj["start_datetime"].strftime("%Y-%m-%d")
            end_date = date_obj["end_datetime"].strftime("%Y-%m-%d")

            schedule_tasks = float_api.get_tasks_by_params(
                'start_date={}&end_date={}'.format(start_date, end_date)
            )
            if len(schedule_tasks) > 0:
                with open('report.csv', 'a') as csv_file:
                    writer = csv.DictWriter(csv_file, fieldnames=fieldnames)

                    report_schedule = {
                        "start_date": start_date,
                        "on_vocation": 0,
                        "in_training_for_teaching": 0,
                        "in_training_for_learning": 0,
                        "onsite_go_live": 0,
                        "onsite_setup": 0,
                        "remote_training": 0
                    }

                    for schedule_task in schedule_tasks:
                        if "paid time off" in schedule_task["name"].lower():
                            report_schedule["on_vocation"] = report_schedule["on_vocation"] + 1

                        if "one on one" in schedule_task["name"].lower():
                            project_item = float_api.get_project_by_id(schedule_task["project_id"])
                            if project_item["name"] is not None:
                                if "trainer" in project_item["name"].lower():
                                    report_schedule["in_training_for_teaching"] = report_schedule["in_training_for_teaching"] + 1
                                if "trainee" in project_item["name"].lower():
                                    report_schedule["in_training_for_learning"] = report_schedule["in_training_for_learning"] + 1

                        if "remote enduser" in schedule_task["name"].lower():
                            report_schedule["remote_training"] = report_schedule["remote_training"] + 1

                        if "enduser" in schedule_task["name"].lower():
                            report_schedule["onsite_setup"] = report_schedule["onsite_setup"] + 1
                    
                        if "go live" in schedule_task["name"].lower():
                            report_schedule["onsite_go_live"] = report_schedule["onsite_go_live"] + 1

                    self.slack_client.api_call(
                        "chat.postMessage",
                        channel=channel,
                        text='Get tasks: {} ~ {}'.format(start_date, end_date)
                    )
                    writer.writerow({
                        "start_date": report_schedule["start_date"],
                        "on_vocation": report_schedule["on_vocation"],
                        "in_training_for_teaching": report_schedule["in_training_for_teaching"],
                        "in_training_for_learning": report_schedule["in_training_for_learning"],
                        "onsite_go_live": report_schedule["onsite_go_live"],
                        "onsite_setup": report_schedule["onsite_setup"],
                        "remote_training": report_schedule["remote_training"],
                    })
                    csv_file.close()

        self.upload('report.csv', channel)


if __name__ == "__main__":
    bot = ScheduleBot()
    bot.run()
